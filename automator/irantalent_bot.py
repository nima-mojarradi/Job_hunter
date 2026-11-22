from pathlib import Path as FilePath
import urllib.parse
import re

from .base_automator import BaseAutomator
from matching.job_parser import parse_job
from matching.scorer import score_resume
from resumes.parser import parse_resume
from database import JobHunterDB


class IranTalentAutomator(BaseAutomator):
    ALREADY_APPLIED_MARKERS = (
        "رزومه قبلا ارسال شده",
        "رزومه قبلاً ارسال شده",
        "درخواست شما ثبت شد",
        "قبلا برای این شغل درخواست",
        "قبلاً برای این شغل درخواست",
        "درخواست شما برای این شغل ثبت شده است",
        "رزومه ارسال شده",
        "ارسال شده",
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
        user_auth_file = f"resumes/{chat_id}/irantalent_state.json"
        
        super().__init__(
            platform_name="IranTalent",
            auth_file=user_auth_file,
            telegram_token=token,
            telegram_chat_id=chat_id,
            headless=headless,
        )
        self.db = JobHunterDB()
        self.user_id = str(chat_id)
        self._profile_resume_uploaded_path = None
        self.db.ensure_user(self.user_id)
        try:
            self.account_id = self.db.upsert_platform_account(self.user_id, "irantalent", session_path=user_auth_file)
        except Exception as exc:
            raise RuntimeError(f"Could not initialize IranTalent account: {exc}") from exc

    def _extract_company_name(self, job_url: str = "", container=None) -> str:
        root = container if container is not None else self.page
        selectors = (
            ".company-name",
            "[class*='company']",
            "a[href*='/company/']",
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

        return "ایران‌تلنت"

    def _is_authenticated(self) -> bool:
        if not self.page or self.page.is_closed():
            return False
        try:
            url = (self.page.url or "").lower()
            if "/auth" in url or "/login" in url or "/signin" in url:
                return False
            
            selectors = ("a[href*='logout']", '.user-profile', '.profile-icon', "a[href*='dashboard']")
            for selector in selectors:
                locator = self.page.locator(selector)
                for index in range(min(locator.count(), 5)):
                    try:
                        if locator.nth(index).is_visible():
                            return True
                    except Exception:
                        continue
            
            body = self.page.locator("body").inner_text(timeout=5000).casefold()
            authenticated_markers = ("خروج", "داشبورد", "رزومه من", "پروفایل من")
            return any(marker in body for marker in authenticated_markers)
        except Exception as exc:
            print(f"[IranTalent] Authentication check failed: {exc}")
            return False

    def login_with_stored_session(self) -> bool:
        if not self.auth_file.is_file():
            print(f"[IranTalent] No session file for user {self.user_id}")
            return False
        self.start_browser(use_saved_session=True)
        try:
            self.page.goto("https://www.irantalent.com/", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2000)
            if self._is_authenticated():
                return True
            print("[IranTalent] Saved session expired or not authenticated.")
            return False
        except Exception as exc:
            print(f"[IranTalent] Session validation failed: {exc}")
            return False

    def login_with_credentials(self, email: str, password: str) -> bool:
        if not email or not password:
            print("[IranTalent] Email and password are required.")
            return False

        self.start_browser(use_saved_session=False)

        def visible_enabled(locator) -> bool:
            try:
                return locator.count() > 0 and locator.first.is_visible() and locator.first.is_enabled()
            except Exception:
                return False

        try:
            self.page.goto(
                "https://www.irantalent.com/auth/login",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            self.page.wait_for_timeout(2500)

            identity_selectors = (
                "input[type='email']",
                "input[type='text']",
                "input[name*='email' i]",
                "input[placeholder*='ایمیل']",
            )

            identity_field = None
            for _ in range(20):
                if self._is_authenticated():
                    self.save_session()
                    return True

                for selector in identity_selectors:
                    locator = self.page.locator(selector)
                    for index in range(min(locator.count(), 4)):
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
                print("[IranTalent] Email input field not found.")
                return False

            identity_field.fill(email)
            self.page.wait_for_timeout(500)

            continue_selectors = (
                "button:has-text('ادامه')",
                "button:has-text('تأیید')",
                "button:has-text('تایید')",
                "button[type='submit']",
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

            password_field = None
            for _ in range(40):
                self.page.wait_for_timeout(500)

                if self._is_authenticated():
                    self.save_session()
                    return True

                password_inputs = self.page.locator("input[type='password']")
                for index in range(min(password_inputs.count(), 3)):
                    candidate = password_inputs.nth(index)
                    try:
                        if candidate.is_visible() and candidate.is_enabled():
                            password_field = candidate
                            break
                    except Exception:
                        continue

                if password_field:
                    break

            if password_field is None:
                print("[IranTalent] Password input field did not appear.")
                return False

            password_field.fill(password)
            self.page.wait_for_timeout(500)

            submitted = False
            password_submit_selectors = (
                "button[type='submit']",
                "button:has-text('ورود')",
                "button:has-text('ادامه')",
                "button:has-text('تأیید')",
            )
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

            for _ in range(120):
                self.page.wait_for_timeout(1000)
                if self._is_authenticated():
                    self.save_session()
                    print("[IranTalent] Login succeeded; session saved.")
                    return True

            print("[IranTalent] Login verification timed out.")
            return False

        except Exception as exc:
            print(f"[IranTalent] Credential login failed: {exc}")
            return False

    def upload_resume_to_profile(self, resume_path: str) -> bool:
        """پاکسازی کامل و اصولی فایل‌های ضمیمه قبلی و بارگذاری رزومه جدید"""
        path_obj = FilePath(resume_path).expanduser().resolve()
        if not path_obj.is_file():
            print(f"[IranTalent] Resume file not found: {path_obj}")
            return False

        try:
            if not self.page or self.page.is_closed():
                self.start_browser(use_saved_session=True)

            print("[IranTalent] Opening candidate CV edit page...")
            self.page.goto(
                "https://www.irantalent.com/candidate/cv/edit",
                wait_until="domcontentloaded",
                timeout=60000
            )
            self.page.wait_for_timeout(3000)

            if "auth" in self.page.url.lower() or "login" in self.page.url.lower():
                print("[IranTalent] Authentication required.")
                return False

            print("[IranTalent] Scrolling to attachments section...")
            self.page.evaluate("window.scrollBy(0, 600)")
            self.page.wait_for_timeout(1000)

            # اصلاح قطعی پاکسازی: پیدا کردن و حذف تک‌تک فایل‌های ضمیمه قبلی به همراه تأییدیه مدال
            try:
                for _ in range(5): # تکرار برای حذف ضمیمه‌های چندگانه احتمالی
                    delete_btns = self.page.locator("xpath=//*[contains(text(), 'فایل‌های ضمیمه')]//following::div[contains(@class, 'attachment') or contains(@class, 'file') or contains(@class, 'item') or self::div]//button[.//i[contains(@class, 'trash') or contains(@class, 'times') or contains(@class, 'remove') or contains(@class, 'close')] or contains(@class, 'delete')] | //button[contains(@class, 'text-danger') or contains(@class, 'btn-danger')]").all()
                    
                    clicked_any = False
                    for btn in delete_btns:
                        try:
                            if btn.is_visible():
                                btn.click()
                                clicked_any = True
                                self.page.wait_for_timeout(1000)
                                
                                confirm_btn = self.page.locator("button:has-text('بله'), button:has-text('حذف'), button:has-text('تایید'), .swal2-confirm").last
                                if confirm_btn.count() > 0 and confirm_btn.is_visible():
                                    confirm_btn.click()
                                    self.page.wait_for_timeout(1000)
                                break
                        except Exception:
                            continue
                    if not clicked_any:
                        break
                print("[IranTalent] Previous attachments cleaned successfully.")
            except Exception as e:
                print(f"[IranTalent] Notice during cleaning old files: {e}")

            # کلیک روی دکمه افزودن ضمیمه جدید (+)
            plus_btn = self.page.locator("xpath=//*[contains(text(), 'فایل‌های ضمیمه')]//ancestor::div[contains(@class, 'card') or contains(@class, 'section') or self::div]//button//i[contains(@class, 'plus')] | //*[contains(text(), 'فایل‌های ضمیمه')]//following::button[1]").first
            
            if plus_btn.count() > 0 and plus_btn.is_visible():
                plus_btn.click()
            else:
                alt_plus = self.page.locator("button:has(.fa-plus), button:has(svg)").filter(has_text=re.compile(r"")).last
                if alt_plus.count() > 0:
                    alt_plus.click()
                else:
                    print("[IranTalent] Attachment add button (+) not found.")
                    return False

            self.page.wait_for_timeout(1500)

            try:
                dropdown = self.page.locator("select, [role='combobox'], .dropdown").first
                if dropdown.count() > 0 and dropdown.is_visible():
                    dropdown.click()
                    self.page.wait_for_timeout(500)
                    resume_option = self.page.get_by_text("رزومه", exact=True)
                    if resume_option.count() > 0:
                        resume_option.first.click()
            except Exception:
                pass

            file_inputs = self.page.locator("input[type='file']")
            if file_inputs.count() == 0:
                print("[IranTalent] File input element not found.")
                return False

            file_input = file_inputs.first
            print(f"[IranTalent] Selecting fresh resume file: {path_obj.name}")
            file_input.set_input_files(str(path_obj), timeout=15000)
            self.page.wait_for_timeout(2000)

            save_btn = self.page.locator("button").filter(has_text=re.compile(r"بارگذاری|ذخیره|تایید|ثبت"))
            if save_btn.count() > 0:
                save_btn.last.click()
                self.page.wait_for_timeout(3000)

            print(f"[IranTalent] Profile updated with single active resume: {path_obj.name}")
            return True

        except Exception as exc:
            print(f"[IranTalent] Resume cleanup/upload failed: {exc}")
            return False

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = (value or "").casefold()
        value = value.replace("ي", "ی").replace("ك", "ک")
        return " ".join(value.split())

    @classmethod
    def is_language_teaching_job(cls, title: str, description: str) -> bool:
        text = cls._normalize_text(f"{title} {description}")

        teaching_phrases = (
            "مدرس زبان",
            "مدرس زبان انگلیسی",
            "مدرس انگلیسی",
            "مدرس آیلتس",
            "مدرس زبان آلمانی",
            "مدرس زبان فرانسه",
            "معلم زبان",
            "معلم انگلیسی",
            "آموزگار زبان",
            "آموزش زبان انگلیسی",
            "آموزش زبان",
            "استاد زبان",
            "مربی زبان",
            "english teacher",
            "language teacher",
            "english instructor",
            "language instructor",
            "ielts instructor",
            "ielts teacher",
            "esl teacher",
            "efl teacher",
            "tefl teacher",
            "english tutor",
            "language tutor",
        )
        return any(phrase in text for phrase in teaching_phrases)

    @classmethod
    def is_remote_job(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        remote_phrases = (
            "دورکاری",
            "دور کاری",
            "دورکار",
            "دور کار",
            "غیرحضوری",
            "غیر حضوری",
            "کار از راه دور",
            "امکان دورکاری",
            "remote",
            "work from home",
            "work remotely",
            "fully remote",
            "۱۰۰ درصد دورکاری",
            "100% remote",
            "online class",
            "online teaching",
            "کلاس آنلاین",
            "تدریس آنلاین",
            "آموزش آنلاین",
        )
        return any(phrase in normalized for phrase in remote_phrases)

    def _find_apply_element(self):
        selectors = "button, a, [role='button'], input[type='submit']"
        try:
            elements = self.page.locator(selectors).all()
        except Exception:
            return None

        for element in elements:
            try:
                if not element.is_visible():
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

    def _submit_application_form(self) -> tuple[bool, str]:
        try:
            self.page.wait_for_timeout(1000)
            body = self._normalize_text(self.page.locator("body").inner_text(timeout=5000))
            
            if any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS):
                return True, "درخواست شما با موفقیت ثبت شد."

            submit_selectors = (
                "button:has-text('ارسال رزومه')", 
                "button:has-text('ثبت درخواست')",
                "button:has-text('Apply')",
            )
            
            submitted = False
            for selector in submit_selectors:
                loc = self.page.locator(selector)
                for i in range(min(loc.count(), 3)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            item.click(timeout=8000)
                            self.page.wait_for_timeout(1500)
                            submitted = True
                            break
                    except Exception:
                        continue
                if submitted:
                    break

            body = self._normalize_text(self.page.locator("body").inner_text(timeout=5000))
            ok = any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS)
            return ok, ("درخواست ایران‌تلنت با موفقیت ثبت شد." if ok else "ارسال درخواست کامل نشد.")
        except Exception as exc:
            return False, f"خطا در ارسال فرم ایران‌تلنت: {exc}"

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
            print(f"[IranTalent] Error parsing resume: {exc}")
            return []

        current_resume_path = str(resume_file.resolve())
        if self._profile_resume_uploaded_path != current_resume_path:
            try:
                uploaded = self.upload_resume_to_profile(current_resume_path)
                if uploaded:
                    self._profile_resume_uploaded_path = current_resume_path
            except Exception as exc:
                print(f"[IranTalent] Profile resume upload warning: {exc}")

        all_collected_jobs = []
        seen_urls = set()

        print(
            f"[IranTalent] Searching keyword '{keyword}' "
            f"for resume '{resume_file.name}'"
        )

        for page_num in range(1, max_pages + 1):
            try:
                if not self.page or self.page.is_closed():
                    self.start_browser(use_saved_session=True)

                # استفاده استاندارد و ایمن از کوئری‌پارامتر برای تمامی صفحات (جلوگیری از فریز شدن و خطای تایپ دستی)
                encoded_keyword = urllib.parse.quote(keyword.strip())
                search_url = f"https://www.irantalent.com/jobs/search?keyword={encoded_keyword}&page={page_num}"
                
                self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(2500)

                # استخراج دقیق لینک‌های آگهی شغلی
                all_links = self.page.locator("a[href*='/jobs/']").all()

                for element in all_links:
                    try:
                        href = element.get_attribute("href")
                        title = " ".join(element.inner_text().split())
                        if not href or not title:
                            continue

                        if "/jobs/search" in href or href.strip() == "https://www.irantalent.com/jobs":
                            continue

                        if href.startswith("/"):
                            href = "https://www.irantalent.com" + href

                        canonical_url = href.split("?", 1)[0]
                        if canonical_url in seen_urls:
                            continue
                        seen_urls.add(canonical_url)
                        all_collected_jobs.append((title, canonical_url))
                    except Exception:
                        continue
            except Exception as exc:
                print(f"[IranTalent] Navigation error on page {page_num}: {exc}")
                continue

        for title, job_url in all_collected_jobs:
            try:
                if not self.page or self.page.is_closed():
                    self.start_browser(use_saved_session=True)

                self.page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(800)

                company_name = self._extract_company_name(job_url)
                
                description_parts = []
                for selector in (".job-description", "[class*='description']", "article"):
                    loc = self.page.locator(selector)
                    for i in range(min(loc.count(), 3)):
                        try:
                            item = loc.nth(i)
                            if item.is_visible():
                                value = item.inner_text(timeout=2000).strip()
                                if len(value) >= 50:
                                    description_parts.append(value)
                        except Exception:
                            continue
                description = max(description_parts, key=len) if description_parts else title
                desc_text = f"عنوان آگهی: {title}\nنام شرکت: {company_name}\n{description}"
                
                is_language_teaching = self.is_language_teaching_job(
                    title=title,
                    description=description,
                )

                if is_language_teaching and not self.is_remote_job(desc_text):
                    print(
                        "[IranTalent] Skipped non-remote language-teaching job: "
                        f"{title}"
                    )
                    continue

                job_data = parse_job(desc_text)
                score_result = score_resume(job_data, resume_profile)
                score = float(score_result.get("total", 0))

                if score < min_score:
                    continue

                existing = self.db.find_application_by_url(self.user_id, job_url)
                if existing and existing.get("status") in {"applied", "under_review", "interview", "rejected", "offer", "hired"}:
                    continue

                self.db.record_application(
                    user_id=self.user_id, job_url=job_url, job_title=title,
                    resume_id=resume_id, jobinja_account_id=self.account_id,
                    keyword=keyword, match_score=score, status="matched", company=company_name,
                    notes="آگهی ایران‌تلنت با حدنصاب تطبیق پیدا کرد.",
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
                success, submit_note = self._submit_application_form()

                status = "applied" if success else "unknown"
                self.db.record_application(
                    user_id=self.user_id, job_url=job_url, job_title=title,
                    resume_id=resume_id, jobinja_account_id=self.account_id,
                    keyword=keyword, match_score=score, status=status, company=company_name, applied=success,
                    notes=submit_note,
                )
                applied_jobs_summary.append({"title": title, "company": company_name, "score": score, "url": job_url, "status": status})

            except Exception as exc:
                print(f"[IranTalent] Error processing job: {exc}")

        return applied_jobs_summary