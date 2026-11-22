import asyncio
import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

app = FastAPI(title="Job Hunter Remote Login")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SESSION_ROOT = Path(os.getenv("JOB_HUNTER_SESSION_DIR", "resumes")).resolve()
INIT_DATA_MAX_AGE = int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", "86400"))
SESSION_IDLE_TIMEOUT = int(os.getenv("JOB_HUNTER_SESSION_IDLE_TIMEOUT", "600"))

PLATFORMS = {
    "jobinja": {
        "name": "جابینجا",
        "url": "https://jobinja.ir/login/user",
        "session": "jobinja_state.json",
    },
    "jobvision": {
        "name": "جاب‌ویژن",
        "url": "https://jobvision.ir/",
        "session": "jobvision_state.json",
    },
    "irantalent": {
        "name": "ایران‌تلنت",
        "url": "https://www.irantalent.com/",
        "session": "irantalent_state.json",
    },
}

LOGIN_PAGE_PATH = Path(__file__).with_name("static") / "login_page.html"
LOGIN_PAGE_HTML = LOGIN_PAGE_PATH.read_text(encoding="utf-8")


def verify_telegram_init_data(init_data: str) -> str:
    if not BOT_TOKEN:
        raise HTTPException(503, "TELEGRAM_BOT_TOKEN روی سرور تنظیم نشده است.")
    if not init_data:
        raise HTTPException(401, "اطلاعات احراز هویت تلگرام ارسال نشده است.")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "امضای تلگرام موجود نیست.")

    auth_date = int(values.get("auth_date", "0"))
    if not auth_date or time.time() - auth_date > INIT_DATA_MAX_AGE:
        raise HTTPException(401, "اعتبار ورود تلگرام منقضی شده است.")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, "امضای اطلاعات تلگرام معتبر نیست.")

    try:
        user = json.loads(values["user"])
        user_id = str(int(user["id"]))
    except Exception:
        raise HTTPException(401, "شناسه کاربر تلگرام معتبر نیست.")
    return user_id


def resolve_user_id(request: Request, fallback_user_id: str | None = None) -> str:
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()
    if init_data:
        return verify_telegram_init_data(init_data)

    candidate = (
        request.headers.get("X-User-Id")
        or request.query_params.get("user_id")
        or fallback_user_id
        or ""
    ).strip()
    digits_only = "".join(ch for ch in candidate if ch.isdigit())
    if digits_only:
        return digits_only

    raise HTTPException(401, "شناسه کاربر تلگرام در دسترس نیست.")


class StartBody(BaseModel):
    platform: str
    user_id: str | None = None
    mobile: bool = False


class InputBody(BaseModel):
    type: str
    x: float | None = None
    y: float | None = None
    deltaY: float | None = None
    key: str | None = None
    text: str | None = Field(default=None, max_length=2000)


@dataclass
class BrowserSession:
    token: str
    user_id: str
    platform: str
    commands: queue.Queue = field(default_factory=queue.Queue)
    ready: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    screenshot: bytes | None = None
    error: str | None = None
    status: str = "starting"
    thread: threading.Thread | None = None
    last_activity: float = field(default_factory=time.monotonic)
    vw: int = 1280
    vh: int = 800


SESSIONS: dict[str, BrowserSession] = {}
SESSIONS_LOCK = threading.Lock()


def session_path(user_id: str, platform: str) -> Path:
    safe_uid = "".join(ch for ch in str(user_id) if ch.isdigit())
    if not safe_uid:
        raise ValueError("Invalid user id")
    user_dir = (SESSION_ROOT / safe_uid).resolve()
    if SESSION_ROOT not in user_dir.parents:
        raise ValueError("Invalid user directory")
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / PLATFORMS[platform]["session"]


def _clamp(val: float | None, limit: float) -> float:
    return max(0.0, min(limit, float(val or 0.0)))


def browser_worker(s: BrowserSession):
    browser = context = None
    try:
        print(f"[RemoteLogin] Launching Chromium for user={s.user_id} platform={s.platform}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": s.vw, "height": s.vh},
                device_scale_factor=1,
                has_touch=True,
                is_mobile=True,
            )
            page = context.new_page()

            # jobinja.ir (ArvanCloud WAF) intermittently aborts the TLS
            # handshake (~20-30% of attempts), so retry before giving up.
            goto_error = None
            for attempt in range(4):
                try:
                    page.goto(
                        PLATFORMS[s.platform]["url"],
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    goto_error = None
                    break
                except Exception as exc:
                    goto_error = exc
                    print(
                        f"[RemoteLogin] page load attempt {attempt + 1}/4 failed "
                        f"for {s.platform}: {exc}"
                    )
                    if attempt < 3:
                        time.sleep(1.5 * (attempt + 1))
            if goto_error is not None:
                raise goto_error

            page.wait_for_timeout(1200)
            s.status = "ready"
            s.ready.set()
            print(f"[RemoteLogin] Browser ready for user={s.user_id} platform={s.platform}")

            max_x, max_y = s.vw - 1, s.vh - 1
            last_capture = 0.0
            while not s.finished.is_set():
                handled = False
                while not s.finished.is_set():
                    try:
                        command = s.commands.get_nowait()
                    except queue.Empty:
                        break

                    handled = True
                    s.last_activity = time.monotonic()
                    kind = command.get("type")
                    try:
                        if kind == "mouse_down":
                            page.mouse.move(_clamp(command.get("x"), max_x), _clamp(command.get("y"), max_y))
                            page.mouse.down()
                        elif kind == "mouse_move":
                            page.mouse.move(_clamp(command.get("x"), max_x), _clamp(command.get("y"), max_y), steps=4)
                        elif kind == "mouse_up":
                            page.mouse.move(_clamp(command.get("x"), max_x), _clamp(command.get("y"), max_y), steps=3)
                            page.mouse.up()
                        elif kind == "click":
                            page.mouse.click(_clamp(command.get("x"), max_x), _clamp(command.get("y"), max_y))
                        elif kind == "wheel":
                            page.mouse.wheel(0, max(-1200, min(1200, float(command.get("deltaY", 0.0)))))
                        elif kind == "type":
                            page.keyboard.insert_text(command["text"])
                        elif kind == "key":
                            allowed = {
                                "Enter", "Tab", "Escape", "Backspace",
                                "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space",
                            }
                            if command.get("key") in allowed:
                                page.keyboard.press(command["key"])
                        elif kind == "complete":
                            target = session_path(s.user_id, s.platform)
                            context.storage_state(path=str(target))
                            try:
                                os.chmod(target, 0o600)
                            except OSError:
                                pass
                            s.status = "saved"
                            s.finished.set()
                    except Exception as exc:
                        s.error = str(exc)

                now = time.monotonic()
                if not s.finished.is_set() and (handled or now - last_capture >= 0.12):
                    try:
                        shot = page.screenshot(type="jpeg", quality=45)
                        with s.lock:
                            s.screenshot = shot
                    except Exception as exc:
                        s.error = f"Screenshot error: {exc}"
                    last_capture = time.monotonic()

                if not handled:
                    time.sleep(0.03)
                    if time.monotonic() - s.last_activity > SESSION_IDLE_TIMEOUT:
                        print(
                            f"[RemoteLogin] Idle session closed "
                            f"user={s.user_id} platform={s.platform}"
                        )
                        s.finished.set()
                        break

    except Exception as exc:
        print(f"[RemoteLogin] Worker error for user={s.user_id}: {exc}")
        s.error = str(exc)
        s.status = "error"
        s.ready.set()
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        s.finished.set()
        if s.status not in {"saved", "error"}:
            s.status = "closed"


def get_session(token: str, user_id: str) -> BrowserSession:
    with SESSIONS_LOCK:
        s = SESSIONS.get(token)
    if not s or s.user_id != user_id:
        raise HTTPException(404, "نشست مرورگر پیدا نشد.")
    return s


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_PAGE_HTML)


@app.get("/health")
async def health():
    return {"ok": True, "service": "job-hunter-remote-login"}


@app.post("/api/auth/start")
async def start_auth(body: StartBody, request: Request):
    user_id = resolve_user_id(request, body.user_id)
    platform = body.platform.strip().lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, "پلتفرم پشتیبانی نمی‌شود.")

    token = secrets.token_urlsafe(32)
    s = BrowserSession(token=token, user_id=user_id, platform=platform)
    if body.mobile:
        # Phone-sized viewport so job sites render their mobile layout,
        # which stays readable inside the Telegram mini-app.
        s.vw, s.vh = 412, 915
    s.thread = threading.Thread(target=browser_worker, args=(s,), daemon=True)
    with SESSIONS_LOCK:
        for old_token, old in list(SESSIONS.items()):
            if old.user_id == user_id and old.platform == platform:
                old.finished.set()
                SESSIONS.pop(old_token, None)
        SESSIONS[token] = s
    s.thread.start()

    if not s.ready.wait(timeout=60):
        s.finished.set()
        raise HTTPException(504, "مرورگر در زمان تعیین‌شده آماده نشد.")
    if s.error:
        raise HTTPException(500, f"اجرای مرورگر ناموفق بود: {s.error}")
    return {"token": token, "status": s.status, "width": s.vw, "height": s.vh}


@app.get("/api/auth/{token}/screen")
async def auth_screen(token: str, request: Request):
    user_id = resolve_user_id(request)
    s = get_session(token, user_id)
    with s.lock:
        shot = s.screenshot
    if not shot:
        raise HTTPException(202, "تصویر هنوز آماده نشده است.")
    return Response(shot, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/auth/{token}/stream")
async def auth_stream(token: str, request: Request):
    """Continuous MJPEG stream: frames are pushed on one connection (no per-frame polling)."""
    user_id = resolve_user_id(request)
    s = get_session(token, user_id)

    async def frames():
        last = None
        while not s.finished.is_set():
            with s.lock:
                shot = s.screenshot
            if shot and shot is not last:
                last = shot
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(shot)).encode() + b"\r\n\r\n"
                    + shot + b"\r\n"
                )
            await asyncio.sleep(0.04)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/auth/{token}/input")
async def auth_input(token: str, body: InputBody, request: Request):
    user_id = resolve_user_id(request)
    s = get_session(token, user_id)
    if s.finished.is_set():
        raise HTTPException(409, "نشست مرورگر بسته شده است.")

    payload = body.model_dump(exclude_none=True)
    if body.type not in {"mouse_down", "mouse_move", "mouse_up", "click", "wheel", "type", "key"}:
        raise HTTPException(400, "نوع فرمان معتبر نیست.")
    if body.type == "type" and not body.text:
        raise HTTPException(400, "متنی برای تایپ ارسال نشده است.")
    s.commands.put(payload)
    return {"ok": True}


@app.post("/api/auth/{token}/complete")
async def auth_complete(token: str, request: Request):
    user_id = resolve_user_id(request)
    s = get_session(token, user_id)
    if s.finished.is_set():
        raise HTTPException(409, "این نشست قبلاً بسته شده است.")
    s.commands.put({"type": "complete"})
    if not s.finished.wait(timeout=15):
        raise HTTPException(504, "ذخیره نشست به پایان نرسید.")
    if s.status != "saved":
        raise HTTPException(500, s.error or "ذخیره نشست ناموفق بود.")
    with SESSIONS_LOCK:
        SESSIONS.pop(token, None)

    if BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": f"✅ نشست {PLATFORMS[s.platform]['name']} ذخیره شد. حالا به ربات برگردید و دکمه شروع را دوباره بزنید.",
                },
                timeout=10,
            )
        except Exception:
            pass

    return {"ok": True, "message": "نشست ذخیره شد. به ربات برگردید و Start را دوباره بزنید."}