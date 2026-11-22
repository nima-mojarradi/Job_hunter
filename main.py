import os
import time
from pathlib import Path

from dotenv import load_dotenv

from automator.jobinja_bot import JobinjaAutomator
from automator.jobvision_bot import JobvisionAutomator
from automator.irantalent_bot import IranTalentAutomator
from automator.telegram_handler import TelegramHandler
from resumes.parser import extract_text, parse_resume
from matching.query_generator import generate_search_queries
from database import JobHunterDB

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN in your .env file.")

DB = JobHunterDB()
PLATFORMS = {
    "jobinja": ("جابینجا", JobinjaAutomator),
    "jobvision": ("جاب‌ویژن", JobvisionAutomator),
    "irantalent": ("ایران‌تلنت", IranTalentAutomator),
}


def clean_and_filter_queries(queries: list[str], profile_key: str) -> list[str]:
    forbidden_words = {
        "google", "meet", "zoom", "tools", "grammar", "vocabulary",
        "github", "telegram", "bot", "fingerprint", "framework",
        "arduino", "esp32", "redis", "celery", "crawler", "authorization",
        "rest", "apis", "django", "python"
    }
    cleaned = []
    for query in queries or []:
        query = " ".join(str(query).split()).strip()
        words = query.casefold().split()
        if not query or len(query) > 40:
            continue
        if any(word in forbidden_words for word in words) and len(words) <= 2:
            continue
        if query.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(query)
    if not cleaned:
        cleaned = [profile_key.replace("_", " ")]
    return cleaned[:7]


def discover_resumes_and_build_profiles(user_id: str) -> dict:
    user_dir = Path("resumes") / str(user_id)
    if not user_dir.is_dir():
        return {}
    pdf_files = sorted(user_dir.glob("*.pdf"), key=lambda p: p.name.casefold())
    if not pdf_files:
        return {}

    DB.ensure_user(user_id)
    profiles = {}
    for pdf_path in pdf_files:
        DB.upsert_resume(
            user_id, pdf_path.name, str(pdf_path.resolve()),
            category=pdf_path.stem.lower(), is_active=True
        )

    for pdf_path in pdf_files:
        profile_key = pdf_path.stem.casefold()
        try:
            resume_text = extract_text(str(pdf_path))
            resume_data = parse_resume(str(pdf_path))
            generated = generate_search_queries(resume_text, resume_data) or {}
            categories = [str(x).casefold() for x in generated.get("categories", [])]
            keywords = clean_and_filter_queries(generated.get("queries", []), profile_key)
            remote_only = (
                "teaching" in categories or "english" in profile_key
                or "teacher" in profile_key
            )
            profiles[profile_key] = {
                "title": f"حوزه {pdf_path.stem}",
                "resume": str(pdf_path.resolve()),
                "keywords": keywords,
                "min_score": 50.0,
                "max_pages": 6,
                "remote_only": remote_only,
            }
            print(f"[Resume] {pdf_path.name}: {len(keywords)} search terms")
        except Exception as exc:
            print(f"[Resume] Could not analyze {pdf_path.name}: {exc}")
    return profiles


def handle_user_login(user_id: str, platform: str, email: str, password: str) -> bool:
    platform = platform.casefold().strip()
    if platform not in PLATFORMS:
        print(f"[Login] Unknown platform: {platform}")
        return False
    automator = None
    try:
        _, automator_class = PLATFORMS[platform]
        automator = automator_class(
            token=TELEGRAM_TOKEN, chat_id=str(user_id), headless=False
        )
        success = bool(automator.login_with_credentials(email, password))
        if not success:
            print(f"[{platform}] Login was not confirmed for user {user_id}.")
            return False
        if not automator.auth_file.is_file() or automator.auth_file.stat().st_size == 0:
            print(f"[{platform}] Login returned success but no usable session file exists.")
            return False
        print(f"[{platform}] Login successful for user {user_id}.")
        return True
    except Exception as exc:
        print(f"[{platform}] Login error for user {user_id}: {exc}")
        return False
    finally:
        if automator:
            automator.close()


def _job_is_active(handler: TelegramHandler, user_id: int) -> bool:
    if not handler._user_active_jobs.get(user_id, False):
        return False
    event = getattr(handler, "_user_stop_events", {}).get(user_id)
    return event is None or not event.is_set()


def _notify(handler: TelegramHandler, text: str, user_id: str):
    handler.send_message(text, str(user_id))


def _process_platform_for_resume(
    handler: TelegramHandler,
    user_id: str,
    platform: str,
    profile: dict,
) -> bool:
    """One browser per platform/resume; reuse it for every keyword, then close it."""
    if not _job_is_active(handler, int(user_id)):
        return False
    label, automator_class = PLATFORMS[platform]
    resume_path = Path(profile["resume"]).resolve()
    automator = None
    try:
        print(f"[JobHunter] {label}: {resume_path.name}")
        automator = automator_class(
            token=TELEGRAM_TOKEN, chat_id=str(user_id), headless=True
        )
        if not automator.login_with_stored_session():
            print(f"[{platform}] Stored session is missing or invalid. Skipping...")
            _notify(
                handler,
                f"⚠️ نشست {label} معتبر نیست یا وجود ندارد؛ از این سایت رد می‌شویم.",
                user_id,
            )
            return True

        reset = getattr(automator, "reset_processed_jobs", None)
        if callable(reset):
            reset()

        for keyword in profile["keywords"]:
            if not _job_is_active(handler, int(user_id)):
                break
            print(f"[{platform}] Searching: {keyword}")
            results = automator.search_and_apply(
                keyword=keyword,
                resume_path=str(resume_path),
                min_score=profile["min_score"],
                max_pages=profile["max_pages"],
                remote_only=profile["remote_only"],
                dry_run=False,
            )
            if results:
                _notify(
                    handler,
                    f"📊 {label} | {resume_path.name} | {keyword}\nنتایج: {len(results)}",
                    user_id,
                )
        return True
    except Exception as exc:
        print(f"[{platform}] Processing error for {resume_path.name}: {exc}")
        _notify(handler, f"⚠️ خطا در {label}: {type(exc).__name__}: {exc}", user_id)
        return True
    finally:
        if automator:
            automator.close()


def run_job_hunter_for_user(handler: TelegramHandler, user_id: str):
    uid = int(user_id)
    print(f"\n[Job Hunter] Starting for user {uid}")
    try:
        while _job_is_active(handler, uid):
            profiles = discover_resumes_and_build_profiles(str(uid))
            if not profiles:
                _notify(handler, "⚠️ هیچ رزومه PDF قابل پردازشی پیدا نشد.", str(uid))
                break

            _notify(
                handler,
                f"🟢 پردازش شروع شد. تعداد رزومه‌ها: {len(profiles)}. "
                "مرورگرها به‌ترتیب و با یک نمونه برای هر سایت اجرا می‌شوند.",
                str(uid),
            )

            for profile in profiles.values():
                if not _job_is_active(handler, uid):
                    break
                resume_path = Path(profile["resume"])
                if not resume_path.is_file():
                    continue
                _notify(
                    handler,
                    f"📁 شروع رزومه: `{resume_path.name}`\n"
                    f"🔍 کلیدواژه‌ها: {', '.join(profile['keywords'])}",
                    str(uid),
                )

                # Sequential platforms keep peak browser memory lower. Each site
                # is authenticated once and reused for all keywords of this resume.
                for platform in PLATFORMS:
                    if not _job_is_active(handler, uid):
                        break
                    if not _process_platform_for_resume(handler, str(uid), platform, profile):
                        break

            if not _job_is_active(handler, uid):
                break
            _notify(
                handler,
                "✅ پردازش تمام رزومه‌ها و سایت‌ها در این دور تمام شد.\n"
                "⏳ دور بعدی ۱۰ دقیقه دیگر شروع می‌شود.",
                str(uid),
            )
            stop_event = getattr(handler, "_user_stop_events", {}).get(uid)
            if stop_event:
                stop_event.wait(600)
            else:
                time.sleep(600)

    except Exception as exc:
        print(f"[Job Hunter] Fatal error for user {uid}: {exc}")
        _notify(handler, f"❌ خطای غیرمنتظره: {type(exc).__name__}: {exc}", str(uid))
    finally:
        handler._user_active_jobs[uid] = False
        print(f"[Job Hunter] Finished for user {uid}")


if __name__ == "__main__":
    print("STARTING BOT...")
    telegram_handler = TelegramHandler(token=TELEGRAM_TOKEN)
    telegram_handler.login_callback = handle_user_login
    telegram_handler.start_job_callback = (
        lambda user_id: run_job_hunter_for_user(telegram_handler, user_id)
    )
    telegram_handler.run_listener()