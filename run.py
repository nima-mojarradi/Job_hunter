import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
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
TUNNEL_OUT_LOG = Path(tempfile.gettempdir()) / "jobhunter_cloudflared.log"
TUNNEL_ERR_LOG = Path(tempfile.gettempdir()) / "jobhunter_cloudflared_err.log"
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def run_fastapi_server():
    print("[FastAPI] Starting Remote Login Server on port 8000...")
    uvicorn.run(
        "job_hunter_remote_login_server:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )


def _cloudflared_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
            capture_output=True, text=True, timeout=15,
        )
        return "cloudflared.exe" in (out.stdout or "").lower()
    except Exception:
        return False


def _start_cloudflared():
    """cloudflared quick-tunnel را با لاگ تازه استارت می‌زند و Popen برمی‌گرداند."""
    cf = shutil.which("cloudflared")
    if not cf:
        print("[Watchdog] cloudflared پیدا نشد؛ مسیر آن در PATH نیست.")
        return None
    for p in (TUNNEL_OUT_LOG, TUNNEL_ERR_LOG):
        try:
            p.write_text("", encoding="utf-8")
        except OSError:
            pass
    out_f = open(TUNNEL_OUT_LOG, "a", encoding="utf-8", errors="ignore")
    err_f = open(TUNNEL_ERR_LOG, "a", encoding="utf-8", errors="ignore")
    kwargs = {"stdout": out_f, "stderr": err_f, "cwd": str(BASE_DIR)}
    if os.name == "nt":
        # مستقل از پروسه ربات: با ری‌استارت ربات، تونل و آدرسش حفظ می‌شود.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", "http://localhost:8000"],
        **kwargs,
    )
    out_f.close()
    err_f.close()
    return proc


def _read_tunnel_url(timeout: float = 90.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = TUNNEL_ERR_LOG.read_text(encoding="utf-8", errors="ignore")
            m = TUNNEL_URL_RE.search(text)
            if m:
                return m.group(0)
        except OSError:
            pass
        time.sleep(2)
    return None


def _verify_tunnel(url: str, timeout: float = 60.0) -> bool:
    """تا تونل واقعاً صفحه لاگین را سرو نکند True برنمی‌گرداند.

    از انتشار آدرسِ تونلی که هنوز برپا نشده (یا DNS قطع است) جلوگیری می‌کند.
    """
    import urllib.request

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


def tunnel_watchdog(handler: TelegramHandler):
    """تونل cloudflared را زنده نگه می‌دارد و آدرس جدید را خودش منتشر می‌کند.

    cloudflared (quick tunnel) با هر بار استارت آدرس جدید می‌گیرد؛ بدون این
    نگهبان، بعد از هر مرگ تونل، دکمه مینی‌اپ ربات به آدرس مرده اشاره می‌کند.

    علاوه بر آن، هر ۱۰ دقیقه دکمه مینی‌اپ را دوباره ثبت می‌کند تا اگر ثبت
    اولیه به‌خاطر قطعی موقت شبکه شکست خورده باشد، خودبه‌خود ترمیم شود.
    """
    print("[Watchdog] نگهبان تونل فعال شد.")
    last_start = 0.0
    last_menu_refresh = 0.0
    warned_no_url = False
    while True:
        time.sleep(20)
        try:
            just_started = False
            if not _cloudflared_running():
                if time.time() - last_start < 180:
                    continue
                last_start = time.time()
                print("[Watchdog] تونل قطع شد؛ در حال راه‌اندازی دوباره...")
                if not _start_cloudflared():
                    continue
                just_started = True
                warned_no_url = False

            url = _read_tunnel_url(timeout=90.0 if just_started else 10.0)
            if not url:
                if just_started and not warned_no_url:
                    print("[Watchdog] آدرس تونل جدید خوانده نشد؛ در چرخه‌های بعد دوباره بررسی می‌شود.")
                    warned_no_url = True
                continue

            current = os.getenv("LOGIN_WEBAPP_URL", "").strip()
            if _full_login_url(url) != current:
                # فقط بعد از اینکه تونل واقعاً جواب داد آدرس منتشر می‌شود
                # (جلوگیری از ثبت آدرس مرده روی دکمه مینی‌اپ تلگرام).
                if not _verify_tunnel(url, timeout=60.0 if just_started else 30.0):
                    if not warned_no_url:
                        print("[Watchdog] تونل هنوز جواب نمی‌دهد؛ آدرس منتشر نشد (تلاش بعدی در چرخه بعد).")
                        warned_no_url = True
                    continue
                warned_no_url = False
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
