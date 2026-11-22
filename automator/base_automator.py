import os
import threading
from pathlib import Path

import telebot
from playwright.sync_api import sync_playwright, Page, BrowserContext


class TelegramNotifier:
    """Small outbound-only Telegram client."""

    def __init__(self, token: str, chat_id: str):
        self.bot = telebot.TeleBot(token, threaded=False)
        self.chat_id = str(chat_id)

    def send_message(self, message: str):
        try:
            self.bot.send_message(self.chat_id, message)
        except Exception as exc:
            print(f"[Telegram] Could not send message: {exc}")


class BaseAutomator:
    """One Playwright runtime per worker thread; each instance owns one browser."""

    _thread_runtime = threading.local()

    def __init__(self, platform_name: str, auth_file: str, telegram_token: str,
                 telegram_chat_id: str, headless: bool = True):
        self.platform_name = platform_name
        self.auth_file = Path(auth_file)
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._runtime_acquired = False

    def _acquire_playwright(self):
        if self._runtime_acquired:
            return
        runtime = getattr(self._thread_runtime, "runtime", None)
        if runtime is None:
            playwright = sync_playwright().start()
            runtime = {"playwright": playwright, "users": 0}
            self._thread_runtime.runtime = runtime
        runtime["users"] += 1
        self.playwright = runtime["playwright"]
        self._runtime_acquired = True

    def _release_playwright(self):
        if not self._runtime_acquired:
            return
        runtime = getattr(self._thread_runtime, "runtime", None)
        self._runtime_acquired = False
        self.playwright = None
        if runtime is None:
            return
        runtime["users"] -= 1
        if runtime["users"] <= 0:
            try:
                runtime["playwright"].stop()
            except Exception:
                pass
            try:
                del self._thread_runtime.runtime
            except AttributeError:
                pass

    def start_browser(self, use_saved_session: bool = True):
        if self.page is not None and not self.page.is_closed():
            return
        if self.context is not None or self.browser is not None:
            self.close()

        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_playwright()
        try:
            self.browser = self.playwright.firefox.launch(headless=self.headless)
            context_args = {
                "viewport": {"width": 1280, "height": 800},
                "locale": "fa-IR",
                "reduced_motion": "reduce",
            }
            if use_saved_session and self.auth_file.is_file():
                try:
                    self.context = self.browser.new_context(
                        storage_state=str(self.auth_file), **context_args
                    )
                    print(f"[{self.platform_name}] Saved session loaded.")
                except Exception as exc:
                    print(f"[{self.platform_name}] Could not load session: {exc}")
                    self.context = self.browser.new_context(**context_args)
            else:
                self.context = self.browser.new_context(**context_args)

            # Do not block images, CSS, or fonts during login. Some sites use them
            # for CAPTCHA, OAuth, or interactive controls.
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(45000)
        except Exception:
            self.close()
            raise

    def save_session(self):
        if not self.context:
            raise RuntimeError("Browser context is not initialized.")
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.auth_file.with_suffix(self.auth_file.suffix + ".tmp")
        try:
            self.context.storage_state(path=str(temp_file))
            os.replace(temp_file, self.auth_file)
            try:
                os.chmod(self.auth_file, 0o600)
            except OSError:
                pass
            print(f"[{self.platform_name}] Session saved.")
        finally:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass

    def close(self):
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None
            self.page = None
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        self._release_playwright()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
