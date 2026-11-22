import os
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

from automator.telegram_handler import TelegramHandler
from main import handle_user_login, run_job_hunter_for_user


def run_fastapi_server():
    print("[FastAPI] Starting Remote Login Server on port 8000...")
    uvicorn.run(
        "job_hunter_remote_login_server:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )


def run_telegram_bot():
    print("[Telegram] Bot listener is starting...")

    handler = TelegramHandler(token=TELEGRAM_TOKEN)
    handler.login_callback = handle_user_login
    handler.start_job_callback = (
        lambda user_id: run_job_hunter_for_user(handler, user_id)
    )

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
