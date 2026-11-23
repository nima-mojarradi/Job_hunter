import os
import threading
import time
from pathlib import Path
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from database import JobHunterDB


class TelegramHandler:
    def __init__(self, token: str, chat_id: str = None):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.bot = telebot.TeleBot(token, threaded=True, num_threads=4)
        self.base_resumes_dir = Path("resumes")
        self.base_resumes_dir.mkdir(parents=True, exist_ok=True)
        self.db = JobHunterDB()
        self._user_states = {}
        self._user_active_jobs = {}
        self._user_threads = {}
        self._user_stop_events = {}
        self.start_job_callback = None
        self.login_callback = None
        self._setup_handlers()
        self._setup_menu_button()

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

    def _sorted_resumes(self, user_dir: Path) -> list:
        """لیست مرتب رزومه‌های PDF تا شماره‌گذاری دکمه‌ها همیشه پایدار بماند."""
        return sorted(user_dir.glob("*.pdf"), key=lambda p: p.name)

    def _setup_menu_button(self, attempts: int = 3, delay: float = 5.0):
        """دکمه‌ی پایین نوار تایپ تلگرام را به مینی‌اپ وصل می‌کند.

        آدرس مینی‌اپ (LOGIN_WEBAPP_URL) در هر بار راه‌اندازی ربات دوباره ثبت
        می‌شود تا با آدرس تونل فعلی همیشه هم‌خوان باشد. چون دسترسی به
        api.telegram.org گاهی متناوب است، در صورت شکست چند بار تلاش می‌شود.
        """
        url = os.getenv("LOGIN_WEBAPP_URL", "").strip()
        if not url:
            print("[Telegram] LOGIN_WEBAPP_URL تنظیم نشده؛ دکمه مینی‌اپ ثبت نشد.")
            return
        menu = MenuButtonWebApp(text="ورود به سایت‌ها", web_app=WebAppInfo(url=url))
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                self.bot.set_chat_menu_button(menu_button=menu)
                print(f"[Telegram] دکمه مینی‌اپ در نوار تایپ ثبت شد (تلاش {attempt}).")
                return
            except Exception as exc:
                last_exc = exc
                print(f"[Telegram] ثبت دکمه مینی‌اپ (تلاش {attempt}/{attempts}) ناموفق: {exc}")
                if attempt < attempts:
                    time.sleep(delay)
        print(f"[Telegram] ثبت دکمه مینی‌اپ پس از {attempts} تلاش ناموفق ماند: {last_exc}")

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
            btn_edit = telebot.types.KeyboardButton("✏️ ویرایش رزومه")
            btn_delete = telebot.types.KeyboardButton("🗑️ حذف رزومه")
            btn_list = telebot.types.KeyboardButton("📋 مشاهده رزومه‌های من")
            btn_download = telebot.types.KeyboardButton("⬇️ دانلود رزومه")
            btn_status = telebot.types.KeyboardButton("🔍 بررسی وضعیت رزومه‌ها")
            btn_login = telebot.types.KeyboardButton("🔑 احراز هویت و لاگین در سایت‌ها")

            is_running = self._user_active_jobs.get(user_id, False)
            btn_job = (
                telebot.types.KeyboardButton("🛑 توقف ارسال رزومه")
                if is_running
                else telebot.types.KeyboardButton("🚀 شروع ارسال خودکار رزومه")
            )

            markup.add(btn_add, btn_edit)
            markup.add(btn_delete, btn_list)
            markup.add(btn_download, btn_status)
            markup.add(btn_login)
            markup.add(btn_job)

            text = "📂 **منوی مدیریت و کنترل هوشمند رزومه‌ها**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:"

        try:
            self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as exc:
            print(f"[Telegram] خطا در ارسال منو: {exc}")

    def _show_paginated_apps(self, chat_id, user_id, rows, page=0, message_id=None):
        per_page = 5
        total_pages = max(1, (len(rows) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        chunk = rows[page * per_page: (page + 1) * per_page]
        labels = {
            "discovered": "پیداشده", "matched": "متناسب", "pending_approval": "در انتظار تأیید",
            "applied": "ارسال‌شده به کارفرما", "under_review": "بررسی‌شده (دیده شده)",
            "interview": "مصاحبه", "rejected": "ردشده", "offer": "پیشنهاد همکاری",
            "hired": "استخدام‌شده", "withdrawn": "انصراف‌داده‌شده", "unknown": "نامشخص",
        }

        lines = []
        for r in chunk:
            company_name = r.get("company") or "شرکت نامشخص"
            status_text = labels.get(r.get("status"), r.get("status"))
            lines.append(
                f"📌 **پوزیشن:** `{r.get('job_title')}`\n"
                f"🏢 **شرکت:** `{company_name}`\n"
                f"📊 **وضعیت:** `{status_text}`\n"
                f"🔗 [لینک آگهی]({r.get('job_url')})\n"
                f"-----------------------------------"
            )

        text_body = (
            f"📋 **وضعیت رزومه‌های ارسالی شما (صفحه {page + 1} از {total_pages}):**\n\n"
            + "\n".join(lines)
        )

        markup = InlineKeyboardMarkup()
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ صفحه قبل", callback_data=f"apps_page:{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("صفحه بعد ➡️", callback_data=f"apps_page:{page + 1}"))
        if nav_buttons:
            markup.row(*nav_buttons)

        try:
            if message_id:
                self.bot.edit_message_text(
                    text_body, chat_id, message_id, parse_mode="Markdown",
                    reply_markup=markup, disable_web_page_preview=True,
                )
            else:
                self.bot.send_message(
                    chat_id, text_body, parse_mode="Markdown",
                    reply_markup=markup, disable_web_page_preview=True,
                )
        except Exception:
            self.bot.send_message(
                chat_id, text_body, parse_mode="Markdown",
                reply_markup=markup, disable_web_page_preview=True,
            )

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
            # تازه‌سازی دکمه مینی‌اپ در پس‌زمینه تا با آدرس تونل فعلی همیشه
            # هم‌خوان بماند (بدون معطل کردن پاسخ /start).
            threading.Thread(
                target=self._setup_menu_button, kwargs={"attempts": 1}, daemon=True
            ).start()
            self.show_main_menu(chat_id, user_id)

        @self.bot.message_handler(
            func=lambda msg: msg.text
            in [
                "📥 آپلود رزومه",
                "➕ افزودن رزومه",
                "✏️ ویرایش رزومه",
                "🗑️ حذف رزومه",
                "📋 مشاهده رزومه‌های من",
                "⬇️ دانلود رزومه",
                "🔍 بررسی وضعیت رزومه‌ها",
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

            elif text == "🗑️ حذف رزومه":
                files = list(user_dir.glob("*.pdf"))
                if not files:
                    self.bot.send_message(chat_id, "رزومه‌ای برای حذف وجود ندارد.")
                    return
                markup = InlineKeyboardMarkup()
                for f in files:
                    markup.add(InlineKeyboardButton(f"❌ حذف {f.name}", callback_data=f"del:{f.name}"))
                self.bot.send_message(
                    chat_id, "🗑️ رزومه‌ای که می‌خواهید حذف شود را انتخاب کنید:", reply_markup=markup
                )

            elif text == "✏️ ویرایش رزومه":
                files = list(user_dir.glob("*.pdf"))
                if not files:
                    self.bot.send_message(chat_id, "رزومه‌ای برای ویرایش وجود ندارد.")
                    return
                markup = InlineKeyboardMarkup()
                for f in files:
                    markup.add(InlineKeyboardButton(f"✏️ ویرایش {f.name}", callback_data=f"edit:{f.name}"))
                self.bot.send_message(
                    chat_id, "🔄 رزومه‌ای که می‌خواهید آپدیت کنید را انتخاب کنید:", reply_markup=markup
                )

            elif text == "⬇️ دانلود رزومه":
                files = self._sorted_resumes(user_dir)
                if not files:
                    self.bot.send_message(chat_id, "هیچ رزومه‌ای برای دانلود وجود ندارد.")
                    return
                markup = InlineKeyboardMarkup()
                for idx, f in enumerate(files):
                    markup.add(InlineKeyboardButton(f"⬇️ {f.name}", callback_data=f"dl:{idx}"))
                self.bot.send_message(
                    chat_id,
                    "📄 روی رزومه‌ای که می‌خواهید بزنید تا فایل همین‌جا برایتان ارسال شود:",
                    reply_markup=markup,
                )

            elif text == "🔍 بررسی وضعیت رزومه‌ها":
                self.bot.send_message(chat_id, "🔄 در حال بررسی و همگام‌سازی وضعیت رزومه‌ها...")
                try:
                    from automator.jobinja_bot import JobinjaAutomator

                    jobinja = JobinjaAutomator(token=self.token, chat_id=str(user_id), headless=True)
                    try:
                        jobinja.sync_applications_status()
                    finally:
                        jobinja.close()
                except Exception as exc:
                    print(f"[Sync Error]: {exc}")

                rows = self.db.list_applications(user_id, limit=50)
                if not rows:
                    self.bot.send_message(chat_id, "هنوز هیچ درخواستی ثبت نشده است.")
                else:
                    self._show_paginated_apps(chat_id, user_id, rows, page=0)

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

        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callbacks(call):
            user_id = call.from_user.id
            user_dir = self._get_user_dir(user_id)
            chat_id = call.message.chat.id
            data = call.data or ""
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                pass

            if data.startswith("apps_page:"):
                try:
                    page_num = int(data.split(":", 1)[1])
                except ValueError:
                    return
                rows = self.db.list_applications(user_id, limit=50)
                if rows:
                    self._show_paginated_apps(
                        chat_id, user_id, rows, page=page_num, message_id=call.message.message_id
                    )

            elif data.startswith("dl:"):
                try:
                    idx = int(data.split(":", 1)[1])
                except ValueError:
                    return
                files = self._sorted_resumes(user_dir)
                if idx < 0 or idx >= len(files):
                    self.bot.send_message(chat_id, "این رزومه دیگر وجود ندارد؛ لیست را دوباره باز کنید.")
                    return
                file_path = files[idx]
                try:
                    with open(file_path, "rb") as fh:
                        self.bot.send_document(
                            chat_id,
                            fh,
                            caption=f"📄 `{file_path.name}`",
                            parse_mode="Markdown",
                            visible_file_name=file_path.name,
                        )
                except Exception as exc:
                    self.bot.send_message(chat_id, f"❌ خطا در ارسال فایل: {exc}")

            elif data.startswith("del:"):
                filename = data.split(":", 1)[1]
                if Path(filename).name != filename or not filename.lower().endswith(".pdf"):
                    self.bot.send_message(chat_id, "نام فایل نامعتبر است.")
                    return
                file_path = user_dir / filename
                if file_path.exists():
                    file_path.unlink()
                    self.db.upsert_resume(
                        user_id, filename, str(file_path),
                        category=file_path.stem.lower(), is_active=False,
                    )
                    self.bot.send_message(
                        chat_id, f"✅ رزومه `{filename}` با موفقیت حذف شد.", parse_mode="Markdown"
                    )
                self.show_main_menu(chat_id, user_id)

            elif data.startswith("edit:"):
                filename = data.split(":", 1)[1]
                if Path(filename).name != filename or not filename.lower().endswith(".pdf"):
                    self.bot.send_message(chat_id, "نام فایل نامعتبر است.")
                    return
                self._user_states[user_id] = f"waiting_for_update_resume:{filename}"
                self.bot.send_message(
                    chat_id,
                    f"📥 لطفاً فایل PDF جدید را برای جایگزینی روی `{filename}` ارسال کنید:",
                    parse_mode="Markdown",
                )

        @self.bot.message_handler(content_types=["document"])
        def handle_documents(message):
            user_id = message.from_user.id
            user_dir = self._get_user_dir(user_id)
            chat_id = message.chat.id
            state = self._user_states.get(user_id)
            file_name = Path(message.document.file_name or "resume.pdf").name

            if not file_name.lower().endswith(".pdf"):
                self.bot.reply_to(message, "⚠️ لطفاً فایل PDF ارسال کنید.")
                return

            try:
                file_info = self.bot.get_file(message.document.file_id)
                downloaded_file = self.bot.download_file(file_info.file_path)

                if state and state.startswith("waiting_for_update_resume:"):
                    target_filename = state.split(":", 1)[1]
                    destination_path = user_dir / target_filename
                    temp_path = user_dir / f".{target_filename}.uploading"
                    with open(temp_path, "wb") as f:
                        f.write(downloaded_file)
                    temp_path.replace(destination_path)
                    self.db.upsert_resume(
                        user_id, destination_path.name, str(destination_path),
                        category=destination_path.stem.lower(), is_active=True,
                    )
                    self._user_states.pop(user_id, None)
                    self.bot.reply_to(
                        message, f"✅ رزومه `{target_filename}` با فایل جدید جایگزین شد."
                    )
                    self.show_main_menu(chat_id, user_id)
                    return

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
