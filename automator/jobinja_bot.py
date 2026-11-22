from pathlib import Path as FilePath
import urllib.parse
import re

from .base_automator import BaseAutomator
from matching.job_parser import parse_job
from matching.scorer import score_resume
from resumes.parser import parse_resume
from database import JobHunterDB


class JobinjaAutomator(BaseAutomator):
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
        user_auth_file = f"resumes/{chat_id}/jobinja_state.json"
        
        super().__init__(
            platform_name="Jobinja",
            auth_file=user_auth_file,
            telegram_token=token,
            telegram_chat_id=chat_id,
            headless=headless,
        )
        self.db = JobHunterDB()
        self.user_id = str(chat_id)
        self._processed_job_urls = set()
        self.db.ensure_user(self.user_id)
        self.account_id = self.db.upsert_jobinja_account(
            self.user_id,
            account_label="Jobinja",
            account_identity="jobinja",
            session_path=str(self.auth_file),
        )

    def reset_processed_jobs(self):
        """شروع چرخه جدید: آگهی‌ها در کلیدواژه‌های یک رزومه تکراری پردازش نشوند."""
        self._processed_job_urls.clear()

    def _extract_company_name(self, job_url: str = "", container=None) -> str:
        """استخراج کاملاً پویا و دقیق نام شرکت برای همه شرکت‌ها از روی لینک URL آگهی"""
        try:
            path = urllib.parse.urlparse(job_url or self.page.url).path
            match = re.search(r"/companies/([^/]+)/jobs/", path, re.IGNORECASE)
            if match:
                slug = urllib.parse.unquote(match.group(1)).strip()
                if slug:
                    clean_slug = re.sub(r'-\d+$', '', slug)
                    return clean_slug.replace("-", " ").title()
        except Exception:
            pass

        root = container if container is not None else self.page
        selectors = (
            "[data-company-name]",
            ".c-jobHeader__company",
            ".c-company-name",
            "a[href*='/companies/']",
        )
        for selector in selectors:
            try:
                elements = root.locator(selector)
                for i in range(min(elements.count(), 5)):
                    element = elements.nth(i)
                    if not element.is_visible():
                        continue
                    value = (element.get_attribute("data-company-name") or element.inner_text()).strip()
                    value = " ".join(value.split())
                    if value and "فرصت" not in value and "نفر" not in value and len(value) < 60:
                        return value.split("|")[0].strip()
            except Exception:
                continue

        return "نامشخص"

    def _is_authenticated(self) -> bool:
        if not self.page or self.page.is_closed():
            return False

        try:
            url = (self.page.url or "").lower()

            if any(x in url for x in (
                "/login", "/signin", "/sign-in",
                "/account/login", "/auth/login"
            )):
                return False

            password_inputs = self.page.locator("input[type='password']")
            for i in range(min(password_inputs.count(), 5)):
                if password_inputs.nth(i).is_visible():
                    return False

            authenticated_selectors = (
                "a[href*='logout']",
                "a[href*='signout']",
                "a[href*='dashboard']",
                ".c-user-menu",
                ".user-profile",
                ".c-header__user",
                "[class*='user-menu']",
                "[class*='user-profile']",
            )

            for selector in authenticated_selectors:
                locator = self.page.locator(selector)
                for i in range(min(locator.count(), 5)):
                    try:
                        if locator.nth(i).is_visible():
                            return True
                    except Exception:
                        continue

            body = self.page.locator("body").inner_text(timeout=5000).lower()

            login_markers = (
                "ورود به حساب",
                "ورود یا ثبت نام",
                "رمز عبور",
            )

            account_markers = (
                "خروج از حساب",
                "خروج از سامانه",
                "داشبورد من",
                "پروفایل من",
                "رزومه من",
            )

            if any(marker in body for marker in account_markers):
                return True

            if any(marker in body for marker in login_markers):
                return False

            return False

        except Exception as exc:
            print(f"[{self.platform_name}] Authentication check failed: {exc}")
            return False

    def login_with_stored_session(self) -> bool:
        if not self.auth_file.is_file():
            print(f"[Jobinja] No session file for this user: {self.auth_file}")
            return False
        self.start_browser(use_saved_session=True)
        try:
            self.page.goto("https://jobinja.ir/", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1200)
            if self._is_authenticated():
                return True
            self.page.goto("https://jobinja.ir/login/user", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            if self._is_authenticated():
                self.save_session()
                return True
            print("[Jobinja] Saved session is expired or not authenticated.")
            return False
        except Exception as exc:
            print(f"[Jobinja] Session validation failed: {exc}")
            return False

    def login_with_credentials(self, email: str, password: str) -> bool:
        if not email or not password:
            return False
        self.start_browser(use_saved_session=False)
        try:
            self.page.goto("https://jobinja.ir/login/user", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            email_selectors = [
                "input[type='email']", "input[name='email']", "input[name='username']",
                "input[name='identity']", "input[autocomplete='username']",
                "input[type='text']"
            ]
            password_selectors = [
                "input[type='password']", "input[name='password']",
                "input[autocomplete='current-password']"
            ]
            email_field = None
            for selector in email_selectors:
                loc = self.page.locator(selector)
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            email_field = item
                            break
                    except Exception:
                        pass
                if email_field:
                    break
            pass_field = None
            for selector in password_selectors:
                loc = self.page.locator(selector)
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            pass_field = item
                            break
                    except Exception:
                        pass
                if pass_field:
                    break

            if email_field and pass_field:
                email_field.fill(email)
                pass_field.fill(password)
                submitted = False
                for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('ورود')", "button:has-text('ورود به حساب')"]:
                    loc = self.page.locator(selector)
                    try:
                        if loc.count() and loc.first.is_visible() and loc.first.is_enabled():
                            loc.first.click(timeout=3000)
                            submitted = True
                            break
                    except Exception:
                        continue
                if not submitted:
                    try:
                        pass_field.press("Enter")
                    except Exception:
                        pass
            else:
                print("[Jobinja] Login fields not recognized; complete login manually in the opened browser.")

            for _ in range(180):
                self.page.wait_for_timeout(1000)
                if self._is_authenticated():
                    self.save_session()
                    print("[Jobinja] Login succeeded; user-specific session saved.")
                    return True
            print("[Jobinja] Login timed out. Check credentials, CAPTCHA, OTP, or page selectors.")
            return False
        except Exception as exc:
            print(f"[Jobinja] Credential login failed: {exc}")
            return False

    def sync_applications_status(self) -> list:
        """بررسی صفحه درخواست‌های ارسالی در جابینجا، استخراج دقیق نام شرکت و به‌روزرسانی وضعیت‌ها"""
        if not self.page:
            self.start_browser(use_saved_session=True)
            
        updated_apps = []
        try:
            self.page.goto("https://jobinja.ir/jobs/applied", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2000)

            job_cards = self.page.locator(".c-jobView, .c-jobApplyCard, [class*='job'], .home2-job").all()
            
            for card in job_cards:
                try:
                    text = card.inner_text().strip()
                    if not text:
                        continue
                        
                    link_elem = card.locator("a[href*='/jobs/']").first
                    if not link_elem.count():
                        continue
                    
                    job_url = link_elem.get_attribute("href")
                    if job_url.startswith("/"):
                        job_url = "https://jobinja.ir" + job_url
                    canonical_url = job_url.split("?", 1)[0]

                    company_name = self._extract_company_name(canonical_url, container=card)

                    status = "applied"
                    if "بررسی‌شده" in text or "مشاهده شده" in text:
                        status = "under_review"
                    elif "مصاحبه" in text:
                        status = "interview"
                    elif "ردشده" in text or "رد شده" in text:
                        status = "rejected"
                    elif "استخدام" in text:
                        status = "hired"

                    existing = self.db.find_application_by_url(self.user_id, canonical_url)
                    if existing:
                        old_status = existing.get("status")
                        self.db.update_application_info_and_status(self.user_id, existing["id"], company_name, status)
                        if old_status != status:
                            updated_apps.append({
                                "title": existing["job_title"],
                                "company": company_name,
                                "old_status": old_status,
                                "new_status": status,
                                "url": canonical_url
                            })
                except Exception:
                    continue

        except Exception as exc:
            print(f"[Jobinja Sync Error]: {exc}")
            
        return updated_apps

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join((value or "").casefold().split())

    def _find_apply_element(self):
        selectors = (
            "button, a, input[type='submit'], input[type='button'], "
            "[role='button'], .c-job-apply__btn, .c-btn"
        )

        try:
            elements = self.page.locator(selectors).all()
        except Exception as exc:
            print(f"[Jobinja] Could not inspect action controls: {exc}")
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

                if (
                    "مشاهده جزئیات درخواست" in text
                    or "مشاهده جزئیات درخواست" in self._normalize_text(text)
                ):
                    return "ALREADY_APPLIED"

                if any(
                    self._normalize_text(marker) in text
                    for marker in self.ALREADY_APPLIED_MARKERS
                ):
                    return "ALREADY_APPLIED"

                if len(text) < 80 and any(
                    self._normalize_text(marker) in text
                    for marker in self.APPLY_MARKERS
                ):
                    return element

            except Exception:
                continue

        try:
            status = self.page.get_by_text(
                "درخواست شما برای این شغل ثبت شده است",
                exact=False,
            )
            if status.count() and status.first.is_visible():
                return "ALREADY_APPLIED"
        except Exception:
            pass

        return None

    def _select_and_upload_resume(self, resume_path: str) -> bool:
        path_obj = FilePath(resume_path).expanduser().resolve()

        if not path_obj.is_file():
            print(f"[Jobinja] Resume file does not exist: {path_obj}")
            return False

        print(f"[Jobinja] Uploading exact file: {path_obj}")
        file_inputs = self.page.locator("input[type='file']")

        try:
            count = file_inputs.count()

            if count == 0:
                print("[Jobinja] No file input found on the application page.")
                return False

            for index in range(count):
                file_input = file_inputs.nth(index)

                try:
                    file_input.set_input_files(str(path_obj))

                    selected_files = file_input.evaluate(
                        "(el) => Array.from(el.files || []).map(f => f.name)"
                    )
                    if path_obj.name in selected_files:
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _submit_application_form(self, resume_path: str) -> tuple[bool, str]:
        """فرم را باز می‌کند، فایل یا رزومه پیش‌فرض را بررسی کرده و نتیجه را ثبت می‌کند."""
        try:
            if not self._is_authenticated():
                return False, "NOT_AUTHENTICATED"
            self.page.wait_for_timeout(1000)
            body = self._normalize_text(self.page.locator("body").inner_text(timeout=5000))
            if any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS):
                return True, "درخواست پس از کلیک اولیه ثبت شده است."

            file_inputs = self.page.locator("input[type='file']")
            if file_inputs.count() > 0:
                if not self._select_and_upload_resume(resume_path):
                    self._close_modal_safely()
                    return False, "فیلد بارگذاری رزومه پیدا شد اما انتخاب فایل موفق نبود."
                self.page.wait_for_timeout(700)

            # بررسی دکمه ارسال نهایی (چه با فایل و چه رزومه پیش‌فرض)
            submit_selectors = (
                "form button[type='submit']", "form input[type='submit']",
                "button[type='submit']", "input[type='submit']",
                "button:has-text('ارسال رزومه')", "button:has-text('ثبت درخواست')",
                "button:has-text('ارسال درخواست')", "button:has-text('درخواست همکاری')",
                "button:has-text('ارسال با رزومه پیش‌فرض')"
            )
            submitted = False
            for selector in submit_selectors:
                loc = self.page.locator(selector)
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            item.click(timeout=10000)
                            self.page.wait_for_timeout(1800)
                            submitted = True
                            break
                    except Exception:
                        continue
                if submitted:
                    break

            body = self._normalize_text(self.page.locator("body").inner_text(timeout=5000))
            ok = any(self._normalize_text(m) in body for m in self.SUCCESS_MARKERS)
            
            if not ok:
                self._close_modal_safely()
                
            return ok, ("فرم با موفقیت ارسال شد." if ok else "دکمه ارسال کلیک شد اما پیام موفقیت دریافت نشد.")
        except Exception as exc:
            self._close_modal_safely()
            return False, f"خطا در تکمیل فرم: {exc}"

    def _close_modal_safely(self):
        """برای جلوگیری از فریز شدن، مودال یا پنجره بازشده را می‌بندد"""
        try:
            close_selectors = [".c-modal__close", "button.close", "[aria-label='Close']", ".modal-close"]
            for selector in close_selectors:
                btn = self.page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    self.page.wait_for_timeout(500)
                    return
            # اگر دکمه بستن پیدا نشد، کلید Escape را فشار می‌دهیم
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

        if not self.page or self.page.is_closed():
            self.start_browser(use_saved_session=True)

        try:
            self.page.goto(
                "https://jobinja.ir/",
                wait_until="domcontentloaded",
                timeout=60000
            )
            self.page.wait_for_timeout(1500)
        except Exception as exc:
            print(f"[Jobinja] Homepage navigation failed: {exc}")
            return []

        if not self._is_authenticated():
            print("[Jobinja] Authentication failed. Skipping all applications.")
            return [{
                "status": "not_authenticated",
                "message": "نشست جابینجا معتبر نیست."
            }]

        resume_file = FilePath(resume_path).expanduser().resolve()

        if not resume_file.is_file():
            print(f"[Jobinja] Resume file does not exist: {resume_file}")
            return []

        print(f"[Jobinja] Using active resume file for user {self.user_id}: {resume_file.name}")

        try:
            resume_profile = parse_resume(str(resume_file))
            resume_id = self.db.upsert_resume(
                self.user_id,
                resume_file.name,
                str(resume_file),
                category=resume_file.stem.lower(),
                is_active=True,
            )
        except Exception as exc:
            print(f"[Jobinja] Error parsing resume: {exc}")
            return []

        all_collected_jobs = []
        seen_urls = set()

        ignored_subpaths = (
            "/jobs/applied",
            "/jobs/saved",
            "/jobs/preferences",
            "/jobs/create",
            "/jobs/following",
        )

        for page_num in range(1, max_pages + 1):
            if remote_only:
                search_url = (
                    f"https://jobinja.ir/jobs?"
                    f"filters%5Bkeywords%5D%5B%5D={encoded_keyword}"
                    f"&filters%5Btelecommuting%5D=1&page={page_num}"
                )
            else:
                search_url = (
                    f"https://jobinja.ir/jobs?"
                    f"filters%5Bkeywords%5D%5B%5D={encoded_keyword}"
                    f"&page={page_num}"
                )

            try:
                self.page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )
                self.page.wait_for_timeout(800)

                all_links = self.page.locator("a[href*='/jobs/']").all()
                page_jobs = []

                for element in all_links:
                    try:
                        href = element.get_attribute("href")
                        title = " ".join(element.inner_text().split())

                        if not href or not title:
                            continue

                        if any(part in href for part in ignored_subpaths):
                            continue

                        if href.startswith("/"):
                            href = "https://jobinja.ir" + href

                        if "jobinja.ir" not in href or "/jobs/" not in href:
                            continue

                        canonical_url = href.split("?", 1)[0]

                        if canonical_url in seen_urls:
                            continue

                        seen_urls.add(canonical_url)
                        page_jobs.append((title, canonical_url))

                    except Exception:
                        continue

                if not page_jobs:
                    break

                all_collected_jobs.extend(page_jobs)

            except Exception as exc:
                print(f"[Jobinja] Search page {page_num} error: {exc}")
                continue

        for index, (title, job_url) in enumerate(all_collected_jobs, start=1):
            if job_url in self._processed_job_urls:
                print(f"[Jobinja] Skipping duplicate job in this resume cycle: {job_url}")
                continue
            self._processed_job_urls.add(job_url)
            print(f"[Jobinja] Processing job {index}/{len(all_collected_jobs)}: {title}")

            try:
                self.page.goto(
                    job_url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )
                self.page.wait_for_timeout(700)

                company_name = self._extract_company_name(job_url)

                selectors = (
                    ".o-box__text.s-jobDesc",
                    ".c-job__description",
                    ".c-job-desc",
                    ".c-jobView__description",
                    ".c-job-detail__description",
                    "[class*='job-description']",
                    "[class*='job__description']",
                    ".o-box__content",
                )

                candidates = []

                for selector in selectors:
                    elements = self.page.locator(selector)
                    count = min(elements.count(), 8)

                    for element_index in range(count):
                        element = elements.nth(element_index)

                        try:
                            if not element.is_visible():
                                continue

                            text = element.inner_text(timeout=3000).strip()

                            if len(text) < 100:
                                continue

                            lowered = self._normalize_text(text)

                            noise_markers = (
                                "ورود به حساب",
                                "ثبت نام",
                                "آگهی های مشابه",
                                "فرصت های شغلی مشابه",
                                "تمام حقوق محفوظ",
                            )

                            noise_count = sum(
                                marker in lowered
                                for marker in noise_markers
                            )

                            candidate_score = min(len(text), 12000) / 100 - noise_count * 15

                            if selector == ".o-box__text.s-jobDesc":
                                candidate_score += 100

                            candidates.append(
                                (candidate_score, text, selector)
                            )

                        except Exception:
                            continue

                if not candidates:
                    continue

                candidates.sort(
                    key=lambda item: item[0],
                    reverse=True
                )

                _, desc_text, _ = candidates[0]

                desc_text = (
                    f"عنوان آگهی: {title}\n"
                    f"نام شرکت: {company_name}\n"
                    f"{desc_text}"
                )

                job_data = parse_job(desc_text)
                score_result = score_resume(job_data, resume_profile)
                score = float(score_result["total"])

                profile_type = score_result.get("profile_type", "")

                if profile_type == "english" or "english" in resume_file.name.lower():
                    combined_job_text = (title + " " + desc_text).lower()

                    remote_keywords = [
                        "دورکاری",
                        "آنلاین",
                        "غیرحضوری",
                        "remote",
                        "online",
                        "telecommuting",
                    ]

                    explicit_onsite = any(
                        term in combined_job_text
                        for term in [
                            "فقط حضوری",
                            "حضوری الزامی",
                            "عدم امکان دورکاری",
                        ]
                    )

                    has_remote = any(
                        kw in combined_job_text
                        for kw in remote_keywords
                    )

                    if explicit_onsite or not has_remote:
                        continue

                if score < min_score:
                    continue

                existing = self.db.find_application_by_url(
                    self.user_id,
                    job_url
                )

                terminal_statuses = {
                    "applied",
                    "under_review",
                    "interview",
                    "rejected",
                    "offer",
                    "hired",
                    "withdrawn",
                    "unknown",
                }

                if existing and existing.get("status") in terminal_statuses:
                    continue

                self.db.record_application(
                    user_id=self.user_id,
                    job_url=job_url,
                    job_title=title,
                    resume_id=resume_id,
                    jobinja_account_id=self.account_id,
                    keyword=keyword,
                    match_score=score,
                    status="matched",
                    company=company_name,
                    notes="آگهی با حدنصاب تطبیق پیدا کرد.",
                )

                if dry_run:
                    applied_jobs_summary.append({
                        "title": title,
                        "company": company_name,
                        "score": score,
                        "url": job_url,
                        "status": "matched",
                    })
                    continue

                apply_control = self._find_apply_element()

                if apply_control == "ALREADY_APPLIED":
                    self.db.record_application(
                        user_id=self.user_id,
                        job_url=job_url,
                        job_title=title,
                        resume_id=resume_id,
                        jobinja_account_id=self.account_id,
                        keyword=keyword,
                        match_score=score,
                        status="applied",
                        company=company_name,
                        applied=True,
                        notes="صفحه جابینجا نشان داد قبلاً درخواست ثبت شده است.",
                    )
                    continue

                if apply_control is None:
                    continue

                try:
                    apply_control.scroll_into_view_if_needed()
                    apply_control.click(timeout=10000)

                    try:
                        self.page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=10000
                        )
                    except Exception:
                        pass

                    success, submit_note = self._submit_application_form(
                        str(resume_file)
                    )

                    status = "applied" if success else "unknown"

                    self.db.record_application(
                        user_id=self.user_id,
                        job_url=job_url,
                        job_title=title,
                        resume_id=resume_id,
                        jobinja_account_id=self.account_id,
                        keyword=keyword,
                        match_score=score,
                        status=status,
                        company=company_name,
                        applied=success,
                        notes=submit_note,
                    )

                    applied_jobs_summary.append({
                        "title": title,
                        "company": company_name,
                        "score": score,
                        "url": job_url,
                        "status": status,
                    })

                except Exception as exc:
                    self.db.record_application(
                        user_id=self.user_id,
                        job_url=job_url,
                        job_title=title,
                        resume_id=resume_id,
                        jobinja_account_id=self.account_id,
                        keyword=keyword,
                        match_score=score,
                        status="unknown",
                        company=company_name,
                        notes="خطا هنگام ارسال درخواست.",
                        last_error=str(exc),
                    )

                    print(f"[Jobinja] Application error: {exc}")

            except Exception as exc:
                print(f"[Jobinja] Error processing job {job_url}: {exc}")
                continue

        return applied_jobs_summary