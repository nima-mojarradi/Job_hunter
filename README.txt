JOB HUNTER - PATCHED FILES

This package is an overlay for the existing Job Hunter project.
It replaces these files:
  main.py
  automator/base_automator.py
  automator/jobinja_bot.py
  automator/jobvision_bot.py
  automator/irantalent_bot.py
  automator/telegram_handler.py

Keep your existing modules and data folders:
  database.py
  resumes/parser.py and resumes/profile.py
  matching/job_parser.py, matching/scorer.py, matching/query_generator.py
  your .env file and resumes/<telegram_user_id>/ PDFs/session files

Install/update dependencies in your existing venv:
  pip install -U playwright pyTelegramBotAPI python-dotenv pymupdf
  playwright install firefox

Copy the files into the existing project root, preserving the automator folder.
Back up the current project first. Do not delete resumes/<user_id> because it
contains PDF resumes and login storage-state files.

Important behavior changes:
- Only one platform browser is opened at a time.
- One automator is reused for every keyword of a given resume/platform pair.
- The old extra login-validation pass was removed; session is checked immediately
  before that site's work starts.
- A failed Jobvision resume upload now stops that site's applications rather
  than risking sending an older profile resume.
- Stop requests are cooperative: Playwright may finish its current navigation
  or bounded operation before stopping.

This code has been syntax-checked, but live login cannot be guaranteed without
running it against the current website DOM and the user's accounts. CAPTCHA,
OTP, OAuth redirects, and changed selectors may still require site-specific
adjustments. Never share storage-state JSON files; they contain active sessions.
