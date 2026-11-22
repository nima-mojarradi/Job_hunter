from pathlib import Path

import os
import threading
from dotenv import load_dotenv

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse

from automator.jobinja_bot import JobinjaAutomator
from automator.jobvision_bot import JobvisionAutomator
from automator.irantalent_bot import IranTalentAutomator

load_dotenv()

STATIC_DIR = Path(__file__).with_name("static")
LOGIN_TEMPLATE_PATH = STATIC_DIR / "login.html"
LOGIN_SUCCESS_PATH = STATIC_DIR / "login_success.html"
HTML_LOGIN_TEMPLATE = LOGIN_TEMPLATE_PATH.read_text(encoding="utf-8")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

app = FastAPI()

VALID_PLATFORMS = {"jobinja", "jobvision", "irantalent"}
PLATFORM_LABELS = {
    "jobinja": "جابینجا",
    "jobvision": "جاب‌ویژن",
    "irantalent": "ایران‌تلنت",
}


def _normalize_platform(platform: str | None) -> str:
    val = (platform or "").strip().lower()
    return val if val in VALID_PLATFORMS else "jobinja"


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    user_id: str = Query(default=""),
    platform: str | None = Query(default=None),
):
    safe_user_id = (user_id or "").strip()
    html = HTML_LOGIN_TEMPLATE.replace("__USER_ID__", safe_user_id)
    if platform and platform.strip().lower() in VALID_PLATFORMS:
        p_val = platform.strip().lower()
        html = html.replace(f'<option value="{p_val}">', f'<option value="{p_val}" selected>')
    return html


@app.post("/login-submit", response_class=HTMLResponse)
async def login_submit(
    user_id: str = Form(default=""),
    platform: str = Form(default="jobinja"),
    email: str = Form(...),
    password: str = Form(...),
):
    clean_user_id = (user_id or "").strip()
    clean_platform = _normalize_platform(platform)
    platform_label = PLATFORM_LABELS[clean_platform]

    def background_login():
        automator = None
        try:
            if clean_platform == "jobinja":
                automator = JobinjaAutomator(
                    token=TELEGRAM_TOKEN, chat_id=clean_user_id, headless=True
                )
            elif clean_platform == "jobvision":
                automator = JobvisionAutomator(
                    token=TELEGRAM_TOKEN, chat_id=clean_user_id, headless=True
                )
            elif clean_platform == "irantalent":
                automator = IranTalentAutomator(
                    token=TELEGRAM_TOKEN, chat_id=clean_user_id, headless=True
                )

            if automator:
                success = automator.login_with_credentials(email, password)
                if success:
                    automator.save_session()
                    print(f"[{clean_platform}] Web login successful for user {clean_user_id}")
        except Exception as e:
            print(f"Web login background error: {e}")
        finally:
            if automator:
                try:
                    automator.close()
                except Exception:
                    pass

    threading.Thread(target=background_login, daemon=True).start()

    html = (LOGIN_SUCCESS_PATH.read_text(encoding="utf-8")
            .replace("__PLATFORM_LABEL__", platform_label)
            .replace("__USER_ID__", clean_user_id))
    return html
