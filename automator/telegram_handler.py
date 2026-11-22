import os
import threading
from pathlib import Path
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from database import JobHunterDB


class TelegramHandler:
    def __init__(self, token: str, chat_id: str = None):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.bot = telebot.TeleBot(token, threaded=True, num_threads=4)
        self.base_resumes_dir = Path("resumes")
        self.base_resumes_dir.mkdir(parents=True, exist_ok=True)
        self.db = JobHunterDB()
        self._user_active_jobs = {}
        self._user_threads = {}
        self._user_stop_events = {}
        self.start_job_callback = None
        self._setup_handlers()

    def send_message(self, message: str, target_chat_id: str = None, reply_markup=None):
        dest_chat = target_chat_id or self.chat_id
        if not dest_chat:
            return
        try:
            self.bot.send_message(
                dest_chat,
                message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            print(f"[Telegram] خطا در ارسال پیام: {exc}")

    def _get_user_dir(self, user_id: int) -> Path:
        user_dir = self.base_resumes_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def show_main_menu(self, chat_id: int, user_id: int):
        user_dir = self._get_user_dir(user_id)
        has_resumes = any(user_dir.glob("*.pdf"))

        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

        if not has_resumes:
            markup.add(telebot.types.KeyboardButton("📥 آپلود رزومه"))
            text = (
                "👋 **به ربات هوشمند مدیریت و ارسال رزومه خوش آمدید!**\n\n"
                "📁 شما هنوز هیچ رزومه‌ای در سیستم ثبت نکرده‌اید.\n"
                "لطفاً برای شروع، فایل PDF رزومه خود را ارسال کنید:"
            )
        else:
            btn_add = telebot.types.KeyboardButton("➕ افزودن رزومه")
            btn_list = telebot.types.KeyboardButton("📋 مشاهده رزومه‌های من")
            btn_login = telebot.types.KeyboardButton("🔑 احراز هویت و لاگین در سایت‌ها")

            is_running = self._user_active_jobs.get(user_id, False)
            btn_job = (
                telebot.types.KeyboardButton("🛑 توقف ارسال رزومه")
                if is_running
                else telebot.types.KeyboardButton("🚀 شروع ارسال خودکار رزومه")
            )

            markup.add(btn_add, btn_list)
            markup.add(btn_login)
            markup.add(btn_job)

            text = "📂 **منوی مدیریت و کنترل هوشمند رزومه‌ها**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:"

        try:
            self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as exc:
            print(f"[Telegram] خطا در ارسال منو: {exc}")

    def _start_user_job(self, user_id: int):
        current = self._user_threads.get(user_id)
        if current and current.is_alive():
            return False
        if not self.start_job_callback:
            return False

        stop_event = threading.Event()
        self._user_stop_events[user_id] = stop_event
        self._user_active_jobs[user_id] = True

        def runner():
            try:
                self.start_job_callback(str(user_id))
            finally:
                stop_event.set()
                self._user_active_jobs[user_id] = False

        thread = threading.Thread(
            target=runner, daemon=True, name=f"job-hunter-{user_id}"
        )
        self._user_threads[user_id] = thread
        thread.start()
        return True

    def _setup_handlers(self):
        @self.bot.message_handler(commands=["start"])
        def handle_start(message):
            user_id = message.from_user.id
            chat_id = message.chat.id
            self.db.ensure_user(user_id)
            self.show_main_menu(chat_id, user_id)

        @self.bot.message_handler(
            func=lambda msg: msg.text
            in [
                "📥 آپلود رزومه",
                "➕ افزودن رزومه",
                "📋 مشاهده رزومه‌های من",
                "🔑 احراز هویت و لاگین در سایت‌ها",
                "🚀 شروع ارسال خودکار رزومه",
                "🛑 توقف ارسال رزومه",
                "🔙 بازگشت به منو",
            ]
        )
        def handle_reply_keyboard(message):
            user_id = message.from_user.id
            chat_id = message.chat.id
            user_dir = self._get_user_dir(user_id)
            text = message.text

            if text in ["📥 آپلود رزومه", "➕ افزودن رزومه"]:
                self.bot.send_message(chat_id, "📥 لطفاً فایل PDF رزومه خود را ارسال کنید:")

            elif text == "📋 مشاهده رزومه‌های من":
                files = list(user_dir.glob("*.pdf"))
                file_list_str = (
                    "\n".join([f"• `{f.name}`" for f in files])
                    if files
                    else "هنوز هیچ رزومه‌ای ثبت نشده است."
                )
                self.bot.send_message(
                    chat_id, f"📋 **لیست رزومه‌های شما:**\n\n{file_list_str}", parse_mode="Markdown"
                )

            elif text == "🔑 احراز هویت و لاگین در سایت‌ها":
                base_url = os.getenv("LOGIN_WEBAPP_URL", "").strip()
                if not base_url:
                    self.bot.send_message(
                        chat_id,
                        "⚠️ آدرس مینی‌اپ تنظیم نشده است. متغیر LOGIN_WEBAPP_URL را در فایل .env قرار دهید.",
                    )
                    return
                separator = "&" if "?" in base_url else "?"
                web_app_url = f"{base_url}{separator}user_id={user_id}"

                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton(
                        "🌐 باز کردن صفحه لاگین (مینی‌اپ)", web_app=WebAppInfo(url=web_app_url)
                    )
                )

                self.bot.send_message(
                    chat_id,
                    "🔐 **احراز هویت از طریق مینی‌اپ:**\n\n"
                    "برای ورود به پلتفرم‌ها، روی دکمه زیر کلیک کنید تا صفحه ورود مستقیماً روی گوشی شما باز شود:",
                    reply_markup=markup,
                    parse_mode="Markdown",
                )

            elif text == "🚀 شروع ارسال خودکار رزومه":
                files = list(user_dir.glob("*.pdf"))
                if not files:
                    self.bot.send_message(chat_id, "⚠️ ابتدا باید حداقل یک رزومه PDF آپلود کنید.")
                    return

                available = []
                missing = []
                if (user_dir / "jobinja_state.json").exists():
                    available.append("جابینجا")
                else:
                    missing.append("جابینجا")

                if (user_dir / "jobvision_state.json").exists():
                    available.append("جاب‌ویژن")
                else:
                    missing.append("جاب‌ویژن")

                if (user_dir / "irantalent_state.json").exists():
                    available.append("ایران‌تلنت")
                else:
                    missing.append("ایران‌تلنت")

                if not available:
                    self.bot.send_message(
                        chat_id,
                        "⚠️ شما هنوز در هیچ‌یک از پلتفرم‌ها لاگین نکرده‌اید.\n"
                        "لطفاً ابتدا از گزینه «🔑 احراز هویت و لاگین در سایت‌ها» وارد شوید.",
                        parse_mode="Markdown",
                    )
                    return

                if self._start_user_job(user_id):
                    msg = (
                        f"🚀 **عملیات ارسال خودکار رزومه‌ها شروع شد!**\n"
                        f"✅ پلتفرم‌های فعال: **{'، '.join(available)}**"
                    )
                    if missing:
                        msg += f"\n⚠️ غیرفعال (نیازمند لاگین): {'، '.join(missing)}"
                    self.bot.send_message(chat_id, msg, parse_mode="Markdown")
                else:
                    self.bot.send_message(chat_id, "⚠️ عملیات از قبل در حال اجراست.")
                self.show_main_menu(chat_id, user_id)

            elif text == "🛑 توقف ارسال رزومه":
                self._user_active_jobs[user_id] = False
                stop_event = self._user_stop_events.get(user_id)
                if stop_event:
                    stop_event.set()
                self.bot.send_message(chat_id, "🛑 درخواست توقف ثبت شد.")
                self.show_main_menu(chat_id, user_id)

            elif text == "🔙 بازگشت به منو":
                self.show_main_menu(chat_id, user_id)

        @self.bot.message_handler(content_types=["document"])
        def handle_documents(message):
            user_id = message.from_user.id
            user_dir = self._get_user_dir(user_id)
            chat_id = message.chat.id
            file_name = Path(message.document.file_name or "resume.pdf").name

            if not file_name.lower().endswith(".pdf"):
                self.bot.reply_to(message, "⚠️ لطفاً فایل PDF ارسال کنید.")
                return

            try:
                file_info = self.bot.get_file(message.document.file_id)
                downloaded_file = self.bot.download_file(file_info.file_path)

                destination_path = user_dir / file_name
                with open(destination_path, "wb") as f:
                    f.write(downloaded_file)
                self.db.upsert_resume(
                    user_id,
                    destination_path.name,
                    str(destination_path),
                    category=destination_path.stem.lower(),
                    is_active=True,
                )

                self.bot.reply_to(message, f"✅ رزومه `{file_name}` با موفقیت ذخیره شد.")
                self.show_main_menu(chat_id, user_id)

            except Exception as exc:
                self.bot.reply_to(message, f"❌ خطا در ذخیره رزومه: {exc}")

    def run_listener(self):
        print("[Telegram] Bot listener is running...")
        self.bot.infinity_polling(timeout=20, long_polling_timeout=20)