import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

from automator.telegram_handler import TelegramHandler
from main import handle_user_login, run_job_hunter_for_user

BASE_DIR = Path(__file__).resolve().parent
TUNNEL_OUT_LOG = Path(tempfile.gettempdir()) / "jobhunter_tunnel.log"
TUNNEL_ERR_LOG = Path(tempfile.gettempdir()) / "jobhunter_tunnel_err.log"
CLOUDFLARED_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# اولویت انتخاب تونل:
#   1) ngrok  — اگر NGROK_AUTHTOKEN تنظیم شده باشد (آدرس ثابت با NGROK_DOMAIN)
#   2) serveo — پیش‌فرض؛ بدون ثبت‌نام، بدون VPN، زیردامنه ثابت (*.serveo.net)
#   3) cloudflared — آخرین انتخاب (نیاز به دسترسی به argotunnel.com دارد)
TUNNEL_PROVIDERS = ("ngrok", "serveo", "cloudflared")
PROVIDER_COOLDOWN = 300.0  # بعد از شکست استارت/تأیید سلامت
RESTART_COOLDOWN = 60.0    # بعد از قطع شدنِ تونلی که سالم کار می‌کرد


def run_fastapi_server():
    print("[FastAPI] Starting Remote Login Server on port 8000...")
    uvicorn.run(
        "job_hunter_remote_login_server:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )


# --------------------------------------------------------------------------- #
#  ابزارهای عمومی تونل
# --------------------------------------------------------------------------- #
def _detached_popen(cmd: list[str], out_log: Path, err_log: Path):
    """اجرای مستقل پروسه‌ی تونل (با لاگ تازه) و برگرداندن Popen."""
    for p in (out_log, err_log):
        try:
            p.write_text("", encoding="utf-8")
        except OSError:
            pass
    out_f = open(out_log, "a", encoding="utf-8", errors="ignore")
    err_f = open(err_log, "a", encoding="utf-8", errors="ignore")
    kwargs = {"stdout": out_f, "stderr": err_f, "cwd": str(BASE_DIR)}
    if os.name == "nt":
        # مستقل از پروسه ربات اجرا می‌شود.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        out_f.close()
        err_f.close()
    return proc


def _kill_proc(proc) -> None:
    if proc is None:
        return
    for fn in ("terminate", "kill"):
        try:
            getattr(proc, fn)()
            break
        except Exception:
            pass
    if os.name == "nt" and proc.pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


def _kill_stray_tunnels() -> None:
    """هر تونلِ باقی‌مانده از اجرای قبلی را می‌کشد تا یکی بیشتر اجرا نشود."""
    for image in ("cloudflared.exe", "ngrok.exe"):
        try:
            subprocess.run(
                ["taskkill", "/IM", image, "/F"],
                capture_output=True, timeout=15,
            )
        except Exception:
            pass
    # ssh باقی‌مانده‌ی serveo (فقط پروسه‌هایی که به serveo.net وصلند)
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Process -Filter \"Name='ssh.exe'\" | "
                "Where-Object { $_.CommandLine -like '*serveo.net*' }).ProcessId",
            ],
            capture_output=True, text=True, timeout=25,
        )
        for pid in (out.stdout or "").split():
            if pid.strip().isdigit():
                subprocess.run(
                    ["taskkill", "/PID", pid.strip(), "/F"],
                    capture_output=True, timeout=10,
                )
    except Exception:
        pass


def _full_login_url(base: str) -> str:
    """آدرس کامل مینی‌اپ: پایه تونل + مسیر /login (روت 404 می‌دهد)."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/login") else base + "/login"


def _publish_tunnel_url(url: str) -> None:
    """آدرس جدید تونل را در .env و محیط فعلی پروسه ثبت می‌کند."""
    url = _full_login_url(url)
    env_path = BASE_DIR / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
        text2, n = re.subn(r"(?m)^LOGIN_WEBAPP_URL=.*$", f"LOGIN_WEBAPP_URL={url}", text)
        if n == 0 and text and not text.endswith("\n"):
            text2 = text + f"\nLOGIN_WEBAPP_URL={url}\n"
            n = 1
        if n:
            env_path.write_text(text2, encoding="utf-8")
    except OSError as exc:
        print(f"[Watchdog] خطا در به‌روزرسانی .env: {exc}")
    # دکمه‌های ربط‌داده‌شده به مینی‌اپ هنگام کلیک از os.getenv می‌خوانند،
    # پس به‌روزرسانی همین‌جا کافی است تا ربات نیاز به ری‌استارت نداشته باشد.
    os.environ["LOGIN_WEBAPP_URL"] = url


def _verify_tunnel(url: str, timeout: float = 60.0) -> bool:
    """تا تونل واقعاً صفحه لاگین را سرو نکند True برنمی‌گرداند.

    از انتشار آدرسِ تونلی که هنوز برپا نشده (یا شبکه قطع است) جلوگیری می‌کند.
    """
    probe = _full_login_url(url) + "?user_id=watchdog"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(probe, timeout=10) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            pass
        time.sleep(5)
    return False


# --------------------------------------------------------------------------- #
#  ارائه‌دهندگان تونل
# --------------------------------------------------------------------------- #
def _provider_ready(name: str) -> bool:
    if name == "ngrok":
        return bool(os.getenv("NGROK_AUTHTOKEN", "").strip()) and bool(
            shutil.which("ngrok")
        )
    if name == "serveo":
        enabled = os.getenv("SERVEO_ENABLED", "1").strip().lower()
        return enabled not in ("0", "false", "no") and bool(shutil.which("ssh"))
    return bool(shutil.which("cloudflared"))


def _start_cloudflared():
    """cloudflared quick-tunnel → (proc, url)."""
    cf = shutil.which("cloudflared")
    if not cf:
        print("[Watchdog] cloudflared پیدا نشد؛ مسیر آن در PATH نیست.")
        return None, None
    proc = _detached_popen(
        [cf, "tunnel", "--url", "http://localhost:8000"],
        TUNNEL_OUT_LOG, TUNNEL_ERR_LOG,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            text = TUNNEL_ERR_LOG.read_text(encoding="utf-8", errors="ignore")
            m = CLOUDFLARED_URL_RE.search(text)
            if m:
                return proc, m.group(0)
        except OSError:
            pass
        if proc.poll() is not None:
            return None, None
        time.sleep(2)
    return proc, None


def _start_ngrok():
    """ngrok (نیازمند NGROK_AUTHTOKEN) → (proc, url).

    با NGROK_DOMAIN آدرس ثابت می‌شود؛ بدون آن آدرس تصادفی است.
    """
    ngrok = shutil.which("ngrok")
    token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    domain = os.getenv("NGROK_DOMAIN", "").strip()
    if not ngrok or not token:
        return None, None
    cmd = [ngrok, "http", "8000", "--authtoken", token]
    if domain:
        cmd += ["--domain", domain]
    proc = _detached_popen(cmd, TUNNEL_OUT_LOG, TUNNEL_ERR_LOG)
    deadline = time.time() + 45
    while time.time() < deadline:
        if proc.poll() is not None:
            return None, None
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:4040/api/tunnels", timeout=5
            ) as resp:
                data = json.load(resp)
            for tun in data.get("tunnels", []):
                public = tun.get("public_url", "")
                if public.startswith("https://"):
                    return proc, public
        except Exception:
            pass
        time.sleep(2)
    return proc, None


def _start_serveo():
    """serveo (SSH reverse-forward، بدون ثبت‌نام) → (proc, url).

    زیردامنه با SERVEO_SUBDOMAIN ثابت می‌ماند؛ اگر اشغال بود، شکست خورده
    ثبت می‌شود و در تلاش بعدی زیردامنه تصادفی امتحان می‌شود.
    """
    ssh = shutil.which("ssh")
    if not ssh:
        return None, None
    sub = os.getenv("SERVEO_SUBDOMAIN", "jobhunter-miniapp").strip()
    if not sub:
        sub = "jobhunter-miniapp"
    # اگر زیردامنه‌ی ثابت یک‌بار شکست خورده باشد، تصادفی امتحان می‌شود
    if getattr(_start_serveo, "_fallback", False):
        sub = f"jobhunter-{int(time.time()) % 100000}"
    cmd = [
        ssh,
        "-R", f"{sub}:80:localhost:8000",
        "-o", "StrictHostKeyChecking=accept-new",
        # توجه: BatchMode اینجا مضر است — serveo احراز keyboard-interactive
        # می‌خواهد و با BatchMode رد می‌شود (Permission denied).
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=25",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=15",
        "-N",
        "serveo.net",
    ]
    proc = _detached_popen(cmd, TUNNEL_OUT_LOG, TUNNEL_ERR_LOG)
    # صبر کن تا یا فوروارد برپا شود یا ssh به‌خاطر اشغال بودن زیردامنه بمیرد.
    # (ExitOnForwardFailure باعث می‌شود اشغال بودن زیردامنه سریع ssh را بکشد.)
    for _ in range(8):
        time.sleep(2)
        if proc.poll() is not None:
            _start_serveo._fallback = True
            return None, None
    _start_serveo._fallback = False
    return proc, f"https://{sub}.serveo.net"


def _start_provider(name: str):
    if name == "ngrok":
        return _start_ngrok()
    if name == "serveo":
        return _start_serveo()
    return _start_cloudflared()


# --------------------------------------------------------------------------- #
#  نگهبان
# --------------------------------------------------------------------------- #
def tunnel_watchdog(handler: TelegramHandler):
    """یکی از تونل‌ها را بالا نگه می‌دارد و آدرس سالم را خودش منتشر می‌کند.

    اولویت با ngrok (اگر توکن داشته باشی) است، بعد serveo (پیش‌فرض، بدون
    ثبت‌نام و بدون VPN) و در نهایت cloudflared. اگر تونلی جواب ندهد، بعد از
    ۵ دقیقه به ارائه‌دهنده‌ی بعدی سوییچ می‌کند.

    علاوه بر آن، هر ۱۰ دقیقه دکمه مینی‌اپ را دوباره ثبت می‌کند تا اگر ثبت
    اولیه به‌خاطر قطعی موقت شبکه شکست خورده باشد، خودبه‌خود ترمیم شود.
    """
    print("[Watchdog] نگهبان تونل فعال شد "
          f"(اولویت: {' ← '.join(TUNNEL_PROVIDERS)}).")
    _kill_stray_tunnels()

    cooldowns: dict[str, float] = {}
    active: str | None = None
    proc = None
    url: str | None = None
    verified = False
    last_menu_refresh = 0.0

    while True:
        time.sleep(20)
        try:
            # --- ۱) سلامت تونل فعال را بررسی کن --------------------------
            if active is not None and proc is not None and proc.poll() is not None:
                print(f"[Watchdog] تونل {active} قطع شد؛ "
                      f"{RESTART_COOLDOWN:.0f} ثانیه بعد دوباره تلاش می‌شود.")
                cooldowns[active] = time.time() + RESTART_COOLDOWN
                active, proc, url, verified = None, None, None, False

            # --- ۲) اگر تونلی فعال نیست، یکی راه بینداز ------------------
            if active is None:
                for name in TUNNEL_PROVIDERS:
                    if not _provider_ready(name):
                        continue
                    if time.time() < cooldowns.get(name, 0.0):
                        continue
                    print(f"[Watchdog] در حال راه‌اندازی تونل {name}...")
                    new_proc, new_url = _start_provider(name)
                    if not new_proc or not new_url:
                        _kill_proc(new_proc)
                        cooldowns[name] = time.time() + PROVIDER_COOLDOWN
                        print(f"[Watchdog] تونل {name} راه نیامد؛ "
                              f"{PROVIDER_COOLDOWN:.0f} ثانیه بعد دوباره، "
                              "یا سراغ گزینه‌ی بعدی می‌رویم.")
                        continue
                    active, proc, url = name, new_proc, new_url
                    verified = False
                    print(f"[Watchdog] تونل {name} بالا آمد: {url}")
                    break
                if active is None:
                    continue

            # --- ۳) تأیید سلامت و انتشار آدرس ---------------------------
            if not verified:
                if not _verify_tunnel(url, timeout=60.0):
                    print(f"[Watchdog] تونل {active} جواب نمی‌دهد؛ "
                          f"کشته شد و {PROVIDER_COOLDOWN:.0f} ثانیه بعد "
                          "ارائه‌دهنده‌ی بعدی امتحان می‌شود.")
                    _kill_proc(proc)
                    cooldowns[active] = time.time() + PROVIDER_COOLDOWN
                    active, proc, url = None, None, None
                    continue
                verified = True

            current = os.getenv("LOGIN_WEBAPP_URL", "").strip()
            if _full_login_url(url) != current:
                _publish_tunnel_url(url)
                print(f"[Watchdog] آدرس تونل به‌روز شد: {_full_login_url(url)}")
                threading.Thread(
                    target=handler._setup_menu_button,
                    kwargs={"attempts": 3, "delay": 5.0},
                    daemon=True,
                ).start()
                last_menu_refresh = time.time()

            # تازه‌سازی دوره‌ای دکمه مینی‌اپ (خودترمیمی در برابر قطعی شبکه)
            if time.time() - last_menu_refresh >= 600:
                last_menu_refresh = time.time()
                threading.Thread(
                    target=handler._setup_menu_button,
                    kwargs={"attempts": 1},
                    daemon=True,
                ).start()
        except Exception as exc:
            print(f"[Watchdog] خطا: {exc}")


def run_telegram_bot():
    print("[Telegram] Bot listener is starting...")

    handler = TelegramHandler(token=TELEGRAM_TOKEN)
    handler.login_callback = handle_user_login
    handler.start_job_callback = (
        lambda user_id: run_job_hunter_for_user(handler, user_id)
    )

    threading.Thread(target=tunnel_watchdog, args=(handler,), daemon=True).start()

    handler.run_listener()


if __name__ == "__main__":
    print("STARTING JOB HUNTER...")

    web_thread = threading.Thread(
        target=run_fastapi_server,
        daemon=True
    )
    web_thread.start()

    try:
        run_telegram_bot()
    except KeyboardInterrupt:
        print("\n[System] Application stopped.")
