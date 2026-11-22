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
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

app = FastAPI(title="Job Hunter Remote Login")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SESSION_ROOT = Path(os.getenv("JOB_HUNTER_SESSION_DIR", "resumes")).resolve()
INIT_DATA_MAX_AGE = int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", "86400"))

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

HTML = r"""
<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>ورود امن به سایت کاریابی</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#101820;color:#eef2f7;font-family:Tahoma,Arial;padding:12px}
main{max-width:1100px;margin:auto}
.panel{background:#1b2733;border:1px solid #344454;border-radius:14px;padding:12px;margin-bottom:10px}
h2{font-size:18px;margin:4px 0 8px}
p{color:#bdc9d5;line-height:1.7;font-size:12.5px;margin:6px 0}
#screen{display:block;width:100%;max-height:65vh;object-fit:contain;background:#080c10;border-radius:10px;touch-action:none;user-select:none;-webkit-user-drag:none}
.controls{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
input,select,button{font:inherit;border:0;border-radius:9px;padding:11px}
input,select{flex:1;min-width:140px;background:#f5f7fa;color:#111}
button{background:#1686d9;color:white;font-weight:bold;cursor:pointer}
button.secondary{background:#475569}
button.done{background:#16834a;width:100%}
#status{font-size:12.5px;color:#a9d8ff;min-height:22px;margin-top:6px;word-break:break-word}
</style>
</head>
<body>
<main>
<section class="panel">
<h2 id="heading">اتصال به حساب کاریابی</h2>
<p>مرورگر روی سرور اجرا می‌شود. اطلاعات ورود و CAPTCHA را مستقیماً در تصویر زیر انجام دهید و پس از ورود موفق، «ثبت ورود و ذخیره نشست» را بزنید.</p>
<div class="controls">
<select id="platform-select" aria-label="انتخاب سایت">
  <option value="jobinja">جابینجا</option>
  <option value="jobvision">جاب‌ویژن</option>
  <option value="irantalent">ایران‌تلنت</option>
</select>
<button type="button" id="connect-btn">اتصال و باز کردن مرورگر</button>
</div>
<div id="status">آماده اتصال...</div>
</section>

<section class="panel">
<img id="screen" alt="نمای زنده مرورگر" draggable="false" />
<div class="controls">
<input id="text" placeholder="برای تایپ در صفحه مرورگر..." autocomplete="off">
<button type="button" id="type">ارسال متن</button>
<button type="button" class="secondary" id="enter">Enter</button>
<button type="button" class="secondary" id="refresh">تازه‌سازی</button>
</div>
<div class="controls">
<button type="button" class="done" id="complete">ثبت ورود و ذخیره نشست</button>
</div>
<p>لمس سریع = کلیک | نگه داشتن و کشیدن = حرکت پیوسته اسلایدر/کپچا | کادر بالا = تایپ.</p>
</section>
</main>

<script>
window.addEventListener("error", function (ev) {
  const el = document.getElementById("status");
  if (el) el.textContent = "خطای صفحه: " + (ev.message || "خطای نامشخص");
});

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
try { tg?.ready(); tg?.expand(); } catch (_) {}

const ALLOWED = ["jobinja", "jobvision", "irantalent"];
const names = { jobinja: "جابینجا", jobvision: "جاب‌ویژن", irantalent: "ایران‌تلنت" };

const params = new URLSearchParams(window.location.search || "");
const urlParam = (params.get("platform") || "").trim().toLowerCase();
const queryUserId = (params.get("user_id") || tg?.initDataUnsafe?.user?.id || "").toString().trim();

let platform = ALLOWED.includes(urlParam) ? urlParam : "jobinja";

const selectEl = document.getElementById("platform-select");
const connectBtn = document.getElementById("connect-btn");
const headingEl = document.getElementById("heading");
const statusEl = document.getElementById("status");
const screen = document.getElementById("screen");

if (selectEl) selectEl.value = platform;

let token = null, timer = null, busy = false, fetchingFrame = false;
let activePointerId = null;
let downClient = null;
let lastCoords = null;
let lastMoveSentAt = 0;
let isDragging = false;

function updateHeading() {
  if (headingEl) headingEl.textContent = "ورود به " + (names[platform] || platform);
}
function setStatus(msg) {
  if (statusEl) statusEl.textContent = msg;
}

function buildHeaders() {
  const h = { "Content-Type": "application/json" };
  if (tg && tg.initData) h["X-Telegram-Init-Data"] = tg.initData;
  if (queryUserId) h["X-User-Id"] = queryUserId;
  return h;
}

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || ("خطای سرور (" + res.status + ")"));
  return data;
}

async function startBrowser() {
  platform = selectEl && ALLOWED.includes(selectEl.value) ? selectEl.value : "jobinja";
  updateHeading();

  if (timer) { clearInterval(timer); timer = null; }
  token = null;
  busy = false;
  fetchingFrame = false;
  activePointerId = null;
  downClient = null;
  lastCoords = null;
  isDragging = false;
  if (screen) screen.removeAttribute("src");

  if (connectBtn) {
    connectBtn.disabled = true;
    connectBtn.textContent = "در حال اتصال...";
  }

  try {
    setStatus("در حال راه‌اندازی مرورگر برای " + names[platform] + "...");
    const data = await api("/api/auth/start", { platform, user_id: queryUserId || undefined });
    token = data.token;
    setStatus("مرورگر آماده است. ورود و CAPTCHA را در تصویر زیر انجام دهید.");
    refreshImage();
    timer = setInterval(refreshImage, 350);
  } catch (err) {
    setStatus("خطا: " + (err && err.message ? err.message : String(err)));
  } finally {
    if (connectBtn) {
      connectBtn.disabled = false;
      connectBtn.textContent = "اتصال و باز کردن مرورگر";
    }
  }
}

function refreshImage() {
  if (!token || busy || fetchingFrame || !screen) return;
  fetchingFrame = true;
  fetch("/api/auth/" + encodeURIComponent(token) + "/screen?t=" + Date.now(), {
    headers: buildHeaders()
  }).then(r => {
    if (r.status === 202) return null;
    if (!r.ok) throw new Error("دریافت تصویر ناموفق بود (" + r.status + ")");
    return r.blob();
  }).then(blob => {
    if (!blob) return;
    const old = screen.dataset.url;
    const url = URL.createObjectURL(blob);
    screen.src = url;
    screen.dataset.url = url;
    if (old) URL.revokeObjectURL(old);
  }).catch(err => setStatus("خطای تصویر: " + err.message))
    .finally(() => { fetchingFrame = false; });
}

async function sendAction(action) {
  if (!token) {
    setStatus("ابتدا روی «اتصال و باز کردن مرورگر» بزنید.");
    return;
  }
  try {
    await api("/api/auth/" + encodeURIComponent(token) + "/input", action);
  } catch (err) {
    setStatus("خطای ارسال فرمان: " + err.message);
  }
}

function clientToBrowserCoords(clientX, clientY) {
  const r = screen.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  const x = Math.max(0, Math.min(1279, (clientX - r.left) * 1280 / r.width));
  const y = Math.max(0, Math.min(799, (clientY - r.top) * 800 / r.height));
  return { x, y };
}

if (connectBtn) connectBtn.addEventListener("click", startBrowser);
if (selectEl) {
  selectEl.addEventListener("change", () => {
    platform = ALLOWED.includes(selectEl.value) ? selectEl.value : "jobinja";
    updateHeading();
  });
}

if (screen) {
  screen.addEventListener("pointerdown", async e => {
    if (!token || activePointerId !== null) return;
    e.preventDefault();
    const pt = clientToBrowserCoords(e.clientX, e.clientY);
    if (!pt) return;
    activePointerId = e.pointerId;
    downClient = { x: e.clientX, y: e.clientY };
    lastCoords = pt;
    lastMoveSentAt = performance.now();
    isDragging = false;
    try { screen.setPointerCapture(e.pointerId); } catch (_) {}
    await sendAction({ type: "mouse_down", x: pt.x, y: pt.y });
  }, { passive: false });

  screen.addEventListener("pointermove", e => {
    if (activePointerId === null || e.pointerId !== activePointerId || !downClient) return;
    e.preventDefault();
    const pt = clientToBrowserCoords(e.clientX, e.clientY);
    if (!pt) return;
    lastCoords = pt;
    const dist = Math.hypot(e.clientX - downClient.x, e.clientY - downClient.y);
    if (dist > 5) isDragging = true;

    const now = performance.now();
    if (isDragging && now - lastMoveSentAt >= 75) {
      lastMoveSentAt = now;
      sendAction({ type: "mouse_move", x: pt.x, y: pt.y });
    }
  }, { passive: false });

  async function finishPointer(e) {
    if (activePointerId === null || e.pointerId !== activePointerId) return;
    e.preventDefault();
    const pt = clientToBrowserCoords(e.clientX, e.clientY) || lastCoords;
    const wasDrag = isDragging;

    activePointerId = null;
    downClient = null;
    lastCoords = null;
    isDragging = false;
    try { screen.releasePointerCapture(e.pointerId); } catch (_) {}

    if (!pt) return;
    if (wasDrag) {
      await sendAction({ type: "mouse_move", x: pt.x, y: pt.y });
      await sendAction({ type: "mouse_up", x: pt.x, y: pt.y });
      setTimeout(refreshImage, 120);
    } else {
      await sendAction({ type: "mouse_up", x: pt.x, y: pt.y });
    }
  }

  screen.addEventListener("pointerup", finishPointer, { passive: false });
  screen.addEventListener("pointercancel", finishPointer, { passive: false });

  screen.addEventListener("wheel", e => {
    e.preventDefault();
    sendAction({ type: "wheel", deltaY: e.deltaY });
  }, { passive: false });
}

document.getElementById("type")?.addEventListener("click", async () => {
  const el = document.getElementById("text");
  if (el && el.value) {
    await sendAction({ type: "type", text: el.value });
    el.value = "";
  }
});
document.getElementById("enter")?.addEventListener("click", () => sendAction({ type: "key", key: "Enter" }));
document.getElementById("refresh")?.addEventListener("click", refreshImage);

document.getElementById("complete")?.addEventListener("click", async () => {
  if (!token || busy) return;
  if (!confirm("آیا ورود به حساب با موفقیت انجام شده است؟")) return;
  busy = true;
  setStatus("در حال ذخیره نشست...");
  try {
    const data = await api("/api/auth/" + encodeURIComponent(token) + "/complete", {});
    clearInterval(timer);
    setStatus(data.message || "نشست ذخیره شد. به ربات برگردید و Start را دوباره بزنید.");
    if (screen) screen.removeAttribute("src");
  } catch (err) {
    setStatus("خطای ذخیره نشست: " + err.message);
    busy = false;
  }
});

updateHeading();
if (tg && tg.initData) {
  startBrowser();
}
</script>
</body>
</html>
"""


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


def _clamp_x(val: float | None) -> float:
    return max(0.0, min(1279.0, float(val or 0.0)))


def _clamp_y(val: float | None) -> float:
    return max(0.0, min(799.0, float(val or 0.0)))


def browser_worker(s: BrowserSession):
    browser = context = None
    try:
        print(f"[RemoteLogin] Launching Chromium for user={s.user_id} platform={s.platform}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
                has_touch=True,
                is_mobile=True,
            )
            page = context.new_page()
            page.goto(PLATFORMS[s.platform]["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            s.status = "ready"
            s.ready.set()
            print(f"[RemoteLogin] Browser ready for user={s.user_id} platform={s.platform}")

            last_capture = 0.0
            while not s.finished.is_set():
                try:
                    command = s.commands.get(timeout=0.05)
                except queue.Empty:
                    command = None

                if command:
                    kind = command.get("type")
                    try:
                        if kind == "mouse_down":
                            page.mouse.move(_clamp_x(command.get("x")), _clamp_y(command.get("y")))
                            page.mouse.down()
                        elif kind == "mouse_move":
                            page.mouse.move(_clamp_x(command.get("x")), _clamp_y(command.get("y")), steps=4)
                        elif kind == "mouse_up":
                            page.mouse.move(_clamp_x(command.get("x")), _clamp_y(command.get("y")), steps=3)
                            page.mouse.up()
                        elif kind == "click":
                            page.mouse.click(_clamp_x(command.get("x")), _clamp_y(command.get("y")))
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
                if now - last_capture >= 0.25 and not s.finished.is_set():
                    try:
                        shot = page.screenshot(type="jpeg", quality=60)
                        with s.lock:
                            s.screenshot = shot
                    except Exception as exc:
                        s.error = f"Screenshot error: {exc}"
                    last_capture = now

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
    return HTMLResponse(HTML)


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
    s.thread = threading.Thread(target=browser_worker, args=(s,), daemon=True)
    with SESSIONS_LOCK:
        for old_token, old in list(SESSIONS.items()):
            if old.user_id == user_id and old.platform == platform:
                old.finished.set()
                SESSIONS.pop(old_token, None)
        SESSIONS[token] = s
    s.thread.start()

    if not s.ready.wait(timeout=25):
        s.finished.set()
        raise HTTPException(504, "مرورگر در زمان تعیین‌شده آماده نشد.")
    if s.error:
        raise HTTPException(500, f"اجرای مرورگر ناموفق بود: {s.error}")
    return {"token": token, "status": s.status}


@app.get("/api/auth/{token}/screen")
async def auth_screen(token: str, request: Request):
    user_id = resolve_user_id(request)
    s = get_session(token, user_id)
    with s.lock:
        shot = s.screenshot
    if not shot:
        raise HTTPException(202, "تصویر هنوز آماده نشده است.")
    return Response(shot, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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