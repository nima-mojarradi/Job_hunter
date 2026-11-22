from pathlib import Path as FilePath
import urllib.parse
import re

from .base_automator import BaseAutomator
from matching.job_parser import parse_job
from matching.scorer import score_resume
from resumes.parser import parse_resume
from database import JobHunterDB


class JobvisionAutomator(BaseAutomator):
    ALREADY_APPLIED_MARKERS = (
        "رزومه قبلا ارسال شده",
        "رزومه قبلاً ارسال شده",
        "درخواست شما ثبت شد",
        "قبلا برای این شغل درخواست",
        "قبلاً برای این شغل درخواست",
        "درخواست شما برای این شغل ثبت شده است",
        "رزومه ارسال شده",
    )

    SUCCESS_MARKERS = (
        "درخواست شما ثبت شد",
        "رزومه شما ارسال شد",
        "با موفقیت ارسال",
        "درخواست همکاری شما ثبت",
    )

    APPLY_MARKERS = (
        "ارسال رزومه",
        "درخواست همکاری",
        "ارسال درخواست",
        "ثبت درخواست",
        "apply",
    )

    def __init__(self, token: str, chat_id: str, headless: bool = True):
        user_auth_file = f"resumes/{chat_id}/jobvision_state.json"
        
        super().__init__(
            platform_name="Jobvision",
            auth_file=user_auth_file,
            telegram_token=token,
            telegram_chat_id=chat_id,
            headless=headless,
        )
        self.db = JobHunterDB()
        self.user_id = str(chat_id)
        # Prevent deleting and re-uploading the same profile resume for every keyword.
        self._profile_resume_uploaded_path = None
        self._processed_job_urls = set()
        self.db.ensure_user(self.user_id)
        try:
            self.account_id = self.db.upsert_platform_account(
                self.user_id, "jobvision", session_path=str(self.auth_file)
            )
        except Exception as exc:
            raise RuntimeError(f"Could not initialize Jobvision account: {exc}") from exc

    def reset_processed_jobs(self):
        """شروع چرخه جدید: آگهی‌ها در کلیدواژه‌های یک رزومه تکراری پردازش نشوند."""
        self._processed_job_urls.clear()

    def _extract_company_name(self, job_url: str = "", container=None) -> str:
        """استخراج نام شرکت از عناصر صفحه به جای استفاده از آیدی عددی URL"""
        root = container if container is not None else self.page
        selectors = (
            ".company-name",
            "[class*='company']",
            "a[href*='/companies/']",
            ".employer-name",
        )
        for selector in selectors:
            try:
                elements = root.locator(selector)
                for i in range(min(elements.count(), 5)):
                    element = elements.nth(i)
                    if not element.is_visible():
                        continue
                    value = element.inner_text().strip()
                    if value and len(value) < 60 and not value.isdigit():
                        return value.split("|")[0].strip()
            except Exception:
                continue

        return "جاب‌ویژن"

    def _is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            url = (self.page.url or "").lower()
            if "/login" in url or "/signin" in url or "/sign-in" in url:
                return False
            login_inputs = self.page.locator("input[type='password'], input[autocomplete='username']")
            for index in range(min(login_inputs.count(), 3)):
                try:
                    if login_inputs.nth(index).is_visible():
                        return False
                except Exception:
                    pass
            selectors = ("a[href*='logout']", '.user-profile', '.c-header__user', "a[href*='dashboard']", "[class*='user-menu']")
            for selector in selectors:
                locator = self.page.locator(selector)
                for index in range(min(locator.count(), 5)):
                    try:
                        if locator.nth(index).is_visible():
                            return True
                    except Exception:
                        continue
            body = self.page.locator("body").inner_text(timeout=5000).casefold()
            authenticated_markers = ("خروج از حساب", "خروج", "داشبورد", "رزومه من", "پروفایل من")
            return any(marker in body for marker in authenticated_markers)
        except Exception as exc:
            print(f"[Jobvision] Authentication check failed: {exc}")
            return False

    def login_with_stored_session(self) -> bool:
        if not self.auth_file.is_file():
            print(f"[Jobvision] No session file for user {self.user_id}")
            return False
        self.start_browser(use_saved_session=True)
        try:
            self.page.goto("https://jobvision.ir/", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2000)
            if self._is_authenticated():
                return True
            self.page.goto("https://jobvision.ir/login", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2000)
            if self._is_authenticated():
                self.save_session()
                return True
            print("[Jobvision] Saved session expired.")
            return False
        except Exception as exc:
            print(f"[Jobvision] Session validation failed: {exc}")
            return False

    def login_with_credentials(self, email: str, password: str) -> bool:
        """Log in through Jobvision's normal flow and allow manual CAPTCHA completion."""
        if not email or not password:
            print("[Jobvision] Email/mobile and password are required.")
            return False

        self.start_browser(use_saved_session=False)

        def visible_enabled(locator) -> bool:
            try:
                return locator.count() > 0 and locator.first.is_visible() and locator.first.is_enabled()
            except Exception:
                return False

        def page_has_oauth_error() -> bool:
            try:
                url = (self.page.url or "").lower()
                body = self.page.locator("body").inner_text(timeout=1500).casefold()
                return (
                    "/home/error" in url
                    or "invalid redirect_uri" in body
                    or "invalid_request" in body
                )
            except Exception:
                return False

        def visible_password_field():
            fields = self.page.locator("input[type='password']")
            for index in range(min(fields.count(), 4)):
                field = fields.nth(index)
                try:
                    if field.is_visible() and field.is_enabled():
                        return field
                except Exception:
                    continue
            return None

        def captcha_error_visible() -> bool:
            try:
                body = self.page.locator("body").inner_text(timeout=1500).casefold()
                return any(marker in body for marker in (
                    "تأیید کپچا ناموفق بود",
                    "تایید کپچا ناموفق بود",
                    "captcha verification failed",
                    "کپچا ناموفق",
                ))
            except Exception:
                return False

        try:
            self.page.goto(
                "https://jobvision.ir/login",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            if page_has_oauth_error():
                print("[Jobvision] Login route returned an OAuth error. Check the site's redirect flow.")
                return False

            identity_selectors = (
                "input[type='email']",
                "input[type='tel']",
                "input[name*='email' i]",
                "input[name*='phone' i]",
                "input[name*='mobile' i]",
                "input[name*='username' i]",
                "input[placeholder*='موبایل']",
                "input[placeholder*='ایمیل']",
                "input[placeholder*='شماره']",
                "input[type='text']",
            )

            identity_field = None
            for _ in range(40):
                if self._is_authenticated():
                    self.save_session()
                    return True
                if page_has_oauth_error():
                    print("[Jobvision] OAuth error detected before identity entry.")
                    return False
                for selector in identity_selectors:
                    locator = self.page.locator(selector)
                    for index in range(min(locator.count(), 6)):
                        candidate = locator.nth(index)
                        try:
                            if candidate.is_visible() and candidate.is_enabled():
                                identity_field = candidate
                                break
                        except Exception:
                            continue
                    if identity_field:
                        break
                if identity_field:
                    break
                self.page.wait_for_timeout(500)

            if identity_field is None:
                print("[Jobvision] Could not find the visible email/mobile field.")
                return False

            identity_field.fill(email)
            continue_selectors = (
                "button:has-text('ادامه')",
                "button:has-text('تأیید')",
                "button:has-text('تایید')",
                "button:has-text('Continue')",
                "button[type='submit']",
                "input[type='submit']",
            )
            clicked = False
            for selector in continue_selectors:
                locator = self.page.locator(selector)
                if visible_enabled(locator):
                    try:
                        locator.first.click(timeout=7000)
                        clicked = True
                        break
                    except Exception:
                        continue
            if not clicked:
                identity_field.press("Enter")

            # Jobvision may reject the first CAPTCHA attempt. Do not try to solve
            # or bypass it: keep the visible browser open so the user can solve it.
            # Wait for the next real step (password/OTP/account) for up to 3 minutes.
            captcha_notice_sent = False
            password_field = None
            for _ in range(180):
                if page_has_oauth_error():
                    print("[Jobvision] OAuth redirect error. CAPTCHA is not the cause of this failure.")
                    return False
                if self._is_authenticated():
                    self.save_session()
                    print("[Jobvision] Login succeeded; session saved.")
                    return True

                password_field = visible_password_field()
                if password_field:
                    break

                if captcha_error_visible() and not captcha_notice_sent:
                    print("[Jobvision] CAPTCHA verification failed. Solve the CAPTCHA manually in the opened browser and click Continue again.")
                    self.telegram.send_message(
                        "⚠️ جاب‌ویژن کپچا را تأیید نکرد. پنجره مرورگر باز است؛ کپچا را دستی دوباره حل کن و روی «ادامه» بزن. ربات تا ۳ دقیقه منتظر می‌ماند."
                    )
                    captcha_notice_sent = True

                self.page.wait_for_timeout(1000)

            if password_field:
                password_field.fill(password)
                password_submit_selectors = (
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('ورود')",
                    "button:has-text('ادامه')",
                    "button:has-text('تأیید')",
                    "button:has-text('تایید')",
                    "button:has-text('Login')",
                )
                submitted = False
                for selector in password_submit_selectors:
                    locator = self.page.locator(selector)
                    if visible_enabled(locator):
                        try:
                            locator.first.click(timeout=7000)
                            submitted = True
                            break
                        except Exception:
                            continue
                if not submitted:
                    password_field.press("Enter")

                for _ in range(180):
                    if page_has_oauth_error():
                        print("[Jobvision] OAuth redirect error after password submission.")
                        return False
                    if self._is_authenticated():
                        self.save_session()
                        print("[Jobvision] Login succeeded; session saved.")
                        return True
                    if captcha_error_visible() and not captcha_notice_sent:
                        print("[Jobvision] CAPTCHA verification failed after password submission.")
                        self.telegram.send_message(
                            "⚠️ جاب‌ویژن دوباره کپچا را رد کرد. کپچا را در پنجره مرورگر دستی حل کن."
                        )
                        captcha_notice_sent = True
                    self.page.wait_for_timeout(1000)

            print("[Jobvision] Login timed out. Check CAPTCHA, OTP, credentials, or the site's login flow.")
            return False

        except Exception as exc:
            print(f"[Jobvision] Credential login failed: {type(exc).__name__}: {exc}")
            return False

    def upload_resume_to_profile(self, resume_path: str) -> bool:
        """Upload or replace the personal resume on Jobvision."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        path_obj = FilePath(resume_path).expanduser().resolve()

        if not path_obj.is_file():
            print(f"[Jobvision] Resume file not found: {path_obj}")
            return False

        try:
            if not self.page or self.page.is_closed():
                self.start_browser(use_saved_session=True)

            print("[Jobvision] Opening personal resume page...")

            self.page.goto(
                "https://jobvision.ir/my-cv",
                wait_until="domcontentloaded",
                timeout=60000
            )
            self.page.wait_for_timeout(3000)

            if "login" in self.page.url.lower():
                print("[Jobvision] Authentication required.")
                return False

            card = self.page.locator("#el-uploadCv")
            card.wait_for(state="visible", timeout=20000)

            # 1. Delete previous resume
            delete_button = card.locator(
                "img[src*='delete-secondary.svg']"
            )

            if delete_button.count() > 0 and delete_button.first.is_visible():
                print("[Jobvision] Existing resume found. Deleting it...")

                delete_button.first.click()
                self.page.wait_for_timeout(1000)

                dialogs = self.page.locator(
                    "[role='dialog'], .modal.show, .modal-dialog"
                )

                visible_dialog = None

                for i in range(dialogs.count()):
                    current = dialogs.nth(i)

                    if current.is_visible():
                        visible_dialog = current
                        break

                if visible_dialog:
                    confirm_button = visible_dialog.locator(
                        "button"
                    ).filter(
                        has_text=re.compile(
                            r"حذف|بله|تأیید|تایید"
                        )
                    )

                    if confirm_button.count() > 0:
                        confirm_button.first.click()
                        print("[Jobvision] Delete confirmed.")
                    else:
                        print("[Jobvision] Delete confirmation button not found.")
                        return False

                try:
                    delete_button.wait_for(
                        state="detached",
                        timeout=15000
                    )
                except PlaywrightTimeoutError:
                    pass

                print("[Jobvision] Previous resume deleted.")

            # 2. Find Add button
            add_button = card.locator(
                "span.text-primary.cursor-pointer"
            ).filter(has_text="افزودن")

            try:
                add_button.first.wait_for(
                    state="visible",
                    timeout=15000
                )
            except PlaywrightTimeoutError:
                print("[Jobvision] Add button not found.")
                return False

            print("[Jobvision] Add button found.")
            add_button.first.click()

            # 3. Wait for upload modal
            modal_text = self.page.get_by_text(
                "برای بارگذاری رزومه شخصی کلیک کنید",
                exact=False
            )

            try:
                modal_text.wait_for(
                    state="visible",
                    timeout=15000
                )
                print("[Jobvision] Upload modal opened.")
            except PlaywrightTimeoutError:
                print("[Jobvision] Upload modal did not appear.")
                return False

            # 4. Find file input globally
            file_inputs = self.page.locator(
                "input[type='file']"
            )

            try:
                file_inputs.first.wait_for(
                    state="attached",
                    timeout=15000
                )
            except PlaywrightTimeoutError:
                print("[Jobvision] No file input found.")
                return False

            file_input = file_inputs.first

            # 5. Select resume
            print(
                f"[Jobvision] Selecting resume: {path_obj.name}"
            )

            file_input.set_input_files(
                str(path_obj),
                timeout=15000
            )
            self.page.wait_for_timeout(1500)
            print(f"[Jobvision] File selected successfully: {path_obj.name}")

            # 6. Find Save Changes button
            save_button = self.page.locator(
                "button"
            ).filter(
                has_text=re.compile(r"ذخیره تغییرات")
            )

            try:
                save_button.last.wait_for(
                    state="visible",
                    timeout=15000
                )
            except PlaywrightTimeoutError:
                print("[Jobvision] Save Changes button not found.")
                return False

            if save_button.last.is_disabled():
                print("[Jobvision] Save Changes button is disabled.")
                return False

            # 7. Save uploaded resume
            save_button.last.click()
            print("[Jobvision] Save Changes clicked.")

            # 8. Wait for modal to close
            try:
                modal_text.wait_for(
                    state="hidden",
                    timeout=30000
                )
            except PlaywrightTimeoutError:
                print("[Jobvision] Upload modal is still open.")
                return False

            # 9. Verify profile
            print("[Jobvision] Verifying uploaded resume...")

            for _ in range(30):
                self.page.wait_for_timeout(1000)

                try:
                    card_text = card.inner_text()

                    has_success_badge = (
                        "100 ٪" in card_text
                        or "100%" in card_text
                    )

                    has_download_link = (
                        "دانلود رزومه شخصی" in card_text
                    )

                    has_delete_button = (
                        card.locator(
                            "img[src*='delete-secondary.svg']"
                        ).count() > 0
                    )

                    if (
                        has_success_badge
                        and has_download_link
                        and has_delete_button
                    ):
                        print(
                            f"[Jobvision] Resume upload completed: "
                            f"{path_obj.name}"
                        )
                        return True

                except Exception:
                    continue

            print("[Jobvision] Upload could not be verified.")
            return False

        except PlaywrightTimeoutError as exc:
            print(
                f"[Jobvision] Timeout during resume upload: {exc}"
            )
            return False

        except Exception as exc:
            print(
                f"[Jobvision] Resume upload failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join((value or "").casefold().split())

    def _find_apply_element(self):
        try:
            # ابتدا بررسی سریع بدنه صفحه برای اطمینان از اینکه قبلا ارسال شده یا خیر
            body_text = self._normalize_text(self.page.locator("body").inner_text(timeout=3000))
            if any(self._normalize_text(marker) in body_text for marker in self.ALREADY_APPLIED_MARKERS):
                return "ALREADY_APPLIED"
        except Exception:
            pass

        selectors = "button, a, [role='button'], input[type='submit'], input[type='button']"
        try:
            elements = self.page.locator(selectors).all()
        except Exception:
            return None

        for element in elements:
            try:
                if not element.is_visible() or not element.is_enabled():
                    continue
                text = element.evaluate(
                    "(el) => (el.innerText || el.textContent || el.value || "
                    "el.getAttribute('aria-label') || '').trim()"
                )
                text = self._normalize_text(text)

                if any(self._normalize_text(marker) in text for marker in self.ALREADY_APPLIED_MARKERS):
                    return "ALREADY_APPLIED"

                if len(text) < 50 and any(self._normalize_text(marker) in text for marker in self.APPLY_MARKERS):
                    return element
            except Exception:
                continue

        return None

    def _submit_application_form(self, resume_path: str) -> tuple[bool, str]:
        try:
            self.page.wait_for_timeout(1000)
            body = self._normalize_text(self.page.locator("body").inner_text(timeout=3000))
            
            if "برای ارسال رزومه لطفا با حساب" in body or "وارد شوید" in body or "login" in self.page.url.lower():
                self._close_modal_safely()
                return False, "NOT_AUTHENTICATED"

            if any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS):
                return True, "درخواست پس از کلیک اولیه ثبت شده است."

            # تلاش برای کلیک روی دکمه‌های تایید احتمالی داخل مودال با تایم‌اوت محافظت شده
            submit_selectors = (
                "button[type='submit']", ".modal-footer button", 
                "button:has-text('ارسال رزومه')", "button:has-text('ثبت درخواست')",
                "button:has-text('تایید و ارسال')", "button:has-text('تایید')"
            )
            
            for selector in submit_selectors:
                loc = self.page.locator(selector)
                for i in range(min(loc.count(), 3)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            item.click(timeout=5000)
                            self.page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue

            body = self._normalize_text(self.page.locator("body").inner_text(timeout=3000))
            ok = any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS)

            # بستن امن هر مودال بازمانده‌ای برای جلوگیری از فریز شدن صفحه بعدی
            self._close_modal_safely()

            return ok, ("درخواست جاب‌ویژن با موفقیت ثبت شد." if ok else "عملیات ارسال در جاب‌ویژن کامل شد یا دکمه نیاز به بررسی دستی دارد.")
        except Exception as exc:
            self._close_modal_safely()
            return False, f"خطا در تکمیل فرم جاب‌ویژن: {exc}"

    def _select_and_upload_resume(self, resume_path: str) -> bool:
        path = FilePath(resume_path).expanduser().resolve()
        if not path.is_file():
            return False
        inputs = self.page.locator("input[type='file']")
        for i in range(inputs.count()):
            item = inputs.nth(i)
            try:
                item.set_input_files(str(path))
                names = item.evaluate("el => Array.from(el.files || []).map(f => f.name)")
                if path.name in names:
                    return True
            except Exception:
                continue
        return False

    def _close_modal_safely(self):
        try:
            close_selectors = [".modal-header button", ".close-icon", "[aria-label*='بستن']", ".c-modal__close", "button:has-text('متوجه شدم')"]
            for selector in close_selectors:
                btn = self.page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    self.page.wait_for_timeout(500)
                    return
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        except Exception:
            pass

    def search_and_apply(
        self,
        keyword: str,
        resume_path: str,
        min_score: float = 50.0,
        max_pages: int = 3,
        remote_only: bool = False,
        dry_run: bool = True,
    ) -> list:
        applied_jobs_summary = []
        encoded_keyword = urllib.parse.quote(keyword)

        resume_file = FilePath(resume_path).expanduser().resolve()
        if not resume_file.is_file():
            return []

        try:
            resume_profile = parse_resume(str(resume_file))
            resume_id = self.db.upsert_resume(
                self.user_id, resume_file.name, str(resume_file),
                category=resume_file.stem.lower(), is_active=True,
            )
        except Exception as exc:
            print(f"[Jobvision] Error parsing resume: {exc}")
            return []

        # Upload the profile resume only once per selected file during this run.
        current_resume_path = str(resume_file.resolve())
        if self._profile_resume_uploaded_path != current_resume_path:
            try:
                uploaded = self.upload_resume_to_profile(current_resume_path)
                if uploaded:
                    self._profile_resume_uploaded_path = current_resume_path
                else:
                    print("[Jobvision] Profile resume upload failed; stopping to avoid applying with an old resume.")
                    return []
            except Exception as exc:
                print(f"[Jobvision] Profile resume upload failed: {exc}")
                return []
        else:
            print("[Jobvision] Resume already uploaded for this file. Skipping upload.")

        all_collected_jobs = []
        seen_urls = set()

        for page_num in range(1, max_pages + 1):
            search_url = f"https://jobvision.ir/jobs/keyword/{encoded_keyword}?page={page_num}"
            try:
                if not self.page or self.page.is_closed():
                    self.start_browser(use_saved_session=True)

                self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(1500)
                
                all_links = self.page.locator("a[href*='/jobs/']").all()

                file_name_lower = resume_file.name.lower()

                for element in all_links:
                    try:
                        href = element.get_attribute("href")
                        title = " ".join(element.inner_text().split())
                        if not href or not title:
                            continue

                        if href.startswith("/"):
                            href = "https://jobvision.ir" + href
                        
                        if "jobvision.ir" not in href or "/jobs/" not in href:
                            continue
                        
                        if not re.search(r'/jobs/\d+/', href) and not re.search(r'/jobs/[^/]+-\d+', href):
                            if "/keyword/" in href or "/type/" in href or "/category/" in href:
                                continue

                        # فیلتر حفاظتی فوق‌العاده سخت‌گیرانه روی عنوان آگهی پیش از کلیک
                        title_lower = title.lower()

                        if "english" in file_name_lower or "teacher" in file_name_lower:
                            forbidden_tech = [
                                "c++", "c#", "python", "developer", "برنامه نویس", "توسعه دهنده", 
                                "توسعه نرم", "engineer", "مهندس", "iot", "امبدد", "back-end", "front-end"
                            ]
                            if any(tech in title_lower for tech in forbidden_tech):
                                continue
                        elif "iot" in file_name_lower or "python" in file_name_lower:
                            forbidden_lang = ["مدرس", "معلم", "زبان", "english teacher", "ielts", "آموزش زبان"]
                            if any(lang in title_lower for lang in forbidden_lang):
                                continue

                        canonical_url = href.split("?", 1)[0]
                        if canonical_url in seen_urls:
                            continue
                        seen_urls.add(canonical_url)
                        all_collected_jobs.append((title, canonical_url))
                    except Exception:
                        continue
            except Exception as exc:
                print(f"[Jobvision] Navigation error on page {page_num}: {exc}")
                continue

        for index, (title, job_url) in enumerate(all_collected_jobs, start=1):
            if job_url in self._processed_job_urls:
                print(f"[Jobvision] Skipping duplicate job in this resume cycle: {job_url}")
                continue
            self._processed_job_urls.add(job_url)
            print(f"[Jobvision] Processing job {index}/{len(all_collected_jobs)}: {title}")

            try:
                if not self.page or self.page.is_closed():
                    self.start_browser(use_saved_session=True)

                self.page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(800)

                company_name = self._extract_company_name(job_url)
                description_parts = []
                for selector in ("[class*='job-description']", "[class*='description']", "[class*='job-detail']", "article"):
                    loc = self.page.locator(selector)
                    for i in range(min(loc.count(), 5)):
                        try:
                            item = loc.nth(i)
                            if item.is_visible():
                                value = item.inner_text(timeout=2500).strip()
                                if len(value) >= 100:
                                    description_parts.append(value)
                        except Exception:
                            continue
                description = max(description_parts, key=len) if description_parts else ""
                if not description:
                    print(f"[Jobvision] Skipping job because description was not extracted: {title} | {job_url}")
                    continue
                desc_text = f"عنوان آگهی: {title}\nنام شرکت: {company_name}\n{description}"
                
                # اعمال شرط ریموت اجباری برای رزومه زبان انگلیسی
                file_name_lower = resume_file.name.lower()
                if "english" in file_name_lower or "teacher" in file_name_lower:
                    combined_job_text = (title + " " + desc_text).lower()
                    remote_terms = ("دورکاری", "غیرحضوری", "آنلاین", "remote", "work from home", "telecommute")
                    onsite_terms = ("فقط حضوری", "حضوری الزامی", "عدم امکان دورکاری")
                    if any(term in combined_job_text for term in onsite_terms) or not any(term in combined_job_text for term in remote_terms):
                        continue

                job_data = parse_job(desc_text)
                score_result = score_resume(job_data, resume_profile)
                score = float(score_result.get("total", 0))

                if score < min_score:
                    continue

                existing = self.db.find_application_by_url(self.user_id, job_url)
                if existing and existing.get("status") in {"applied", "under_review", "interview", "rejected", "offer", "hired", "withdrawn", "unknown"}:
                    continue

                self.db.record_application(
                    user_id=self.user_id, job_url=job_url, job_title=title,
                    resume_id=resume_id, jobinja_account_id=self.account_id,
                    keyword=keyword, match_score=score, status="matched", company=company_name,
                    notes="آگهی با حدنصاب تطبیق پیدا کرد.",
                )

                if dry_run:
                    applied_jobs_summary.append({"title": title, "company": company_name, "score": score, "url": job_url, "status": "matched"})
                    continue

                apply_control = self._find_apply_element()
                if apply_control == "ALREADY_APPLIED":
                    continue
                if apply_control is None:
                    continue

                apply_control.scroll_into_view_if_needed()
                apply_control.click(timeout=10000)
                success, submit_note = self._submit_application_form(str(resume_file))
                
                if submit_note == "NOT_AUTHENTICATED":
                    print("[Jobvision] Authentication required. Stopping application loop.")
                    try:
                        self.telegram.send_message("⚠️ نشست (سشن) جاب‌ویژن شما منقضی شده یا لاگین نیستید. لطفاً مجدداً احراز هویت کنید.")
                    except Exception:
                        pass
                    break

                status = "applied" if success else "unknown"

                self.db.record_application(
                    user_id=self.user_id, job_url=job_url, job_title=title,
                    resume_id=resume_id, jobinja_account_id=self.account_id,
                    keyword=keyword, match_score=score, status=status, company=company_name, applied=success,
                    notes=submit_note,
                )
                applied_jobs_summary.append({"title": title, "company": company_name, "score": score, "url": job_url, "status": status})

            except Exception as exc:
                print(f"[Jobvision] Error processing job: {exc}")

        return applied_jobs_summary