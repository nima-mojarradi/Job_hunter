import os
import threading
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse

from automator.jobinja_bot import JobinjaAutomator
from automator.jobvision_bot import JobvisionAutomator
from automator.irantalent_bot import IranTalentAutomator

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

app = FastAPI()

VALID_PLATFORMS = {"jobinja", "jobvision", "irantalent"}
PLATFORM_LABELS = {
    "jobinja": "جابینجا",
    "jobvision": "جاب‌ویژن",
    "irantalent": "ایران‌تلنت",
}

HTML_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ورود به پلتفرم‌های کاریابی</title>
    <style>
        body { font-family: Tahoma, sans-serif; background: #f4f4f9; padding: 20px; text-align: center; margin: 0; }
        .card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); max-width: 400px; margin: auto; }
        input, select, button { width: 100%; padding: 14px; margin: 10px 0; border: 1px solid #ddd; border-radius: 8px; box-sizing: border-box; font-size: 14px; }
        button { background: #0088cc; color: white; border: none; font-size: 16px; cursor: pointer; font-weight: bold; }
        button:hover { background: #006699; }
        h2 { color: #333; margin-top: 0; }
        p { color: #666; font-size: 13px; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🔑 احراز هویت پلتفرم</h2>
        <p>پلتفرم مورد نظر را انتخاب کرده و اطلاعات حساب کاربری خود را وارد کنید.</p>
        <form action="/login-submit" method="post">
            <input type="hidden" name="user_id" value="__USER_ID__">
            <select name="platform" id="platform-select">
                <option value="jobinja">جابینجا</option>
                <option value="jobvision">جاب‌ویژن</option>
                <option value="irantalent">ایران‌تلنت</option>
            </select>
            <input type="text" name="email" placeholder="ایمیل یا شماره موبایل" required>
            <input type="password" name="password" placeholder="رمز عبور" required>
            <button type="submit">تایید و ورود به حساب</button>
        </form>
    </div>
    <script>
        (function () {
            const params = new URLSearchParams(window.location.search);
            const p = (params.get("platform") || "").toLowerCase();
            const allowed = ["jobinja", "jobvision", "irantalent"];
            const select = document.getElementById("platform-select");
            if (select && allowed.includes(p)) {
                select.value = p;
            }
        })();
    </script>
</body>
</html>
"""


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

    return f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ثبت درخواست ورود</title>
    </head>
    <body style='text-align:center; font-family:Tahoma; direction:rtl; background:#f4f4f9; padding:40px; margin:0;'>
        <div style='background:white; padding:30px; border-radius:12px; max-width:400px; margin:auto; box-shadow:0 4px 15px rgba(0,0,0,0.1);'>
            <h2 style='color: #28a745; margin-top:0;'>✅ درخواست ورود ({platform_label}) ثبت شد!</h2>
            <p style='color: #555; line-height: 1.6;'>عملیات لاگین برای <strong>{platform_label}</strong> در پس‌زمینه سرور در حال انجام است. می‌توانید حساب بعدی را ثبت کنید یا به تلگرام برگردید.</p>
            <a href="/login?user_id={clean_user_id}" style="display:inline-block; margin-top:15px; padding:12px 20px; background:#0088cc; color:white; text-decoration:none; border-radius:8px; font-weight:bold;">➕ ثبت حساب پلتفرم بعدی</a>
        </div>
    </body>
    </html>
    """