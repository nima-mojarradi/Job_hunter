# Job Hunter

An automated job-application assistant controlled from **Telegram**. It parses your
resume PDFs, generates search queries, scores listings on Iranian job boards, and
submits applications through a real browser — while recording every application and
its status in a local SQLite database.

> **Disclaimer:** this project automates interactions with third-party websites.
> Use it at your own risk and only on accounts you own. Site layouts, CAPTCHA,
> OTP, and login flows change without notice; selectors may need updating.
> Always test with `dry_run=True` first, and never share your `*_state.json`
> session files — they contain active login sessions.

## Features

- **Telegram bot interface** — upload resumes, start/stop jobs, and get progress
  notifications in Persian, all from chat.
- **Multi-platform automation** — Jobinja, Jobvision, and IranTalent via Playwright
  (Firefox), one browser at a time to keep memory low.
- **Resume-driven search** — each PDF becomes a profile; a query generator derives
  up to 7 search keywords per resume (Persian and English).
- **Scoring engine** — resumes are matched against listings with weighted scoring
  per profile type (`python`, `iot`, `english`); only jobs above `min_score`
  (default 50) are applied to.
- **Remote login mini-app** — a FastAPI service streams a live browser view inside
  Telegram so you can log in and solve CAPTCHA yourself; the session is saved
  server-side and reused afterwards.
- **Application tracking** — every application is stored with status history
  (`discovered → matched → applied → under_review → interview → …`) and can be
  synced back from Jobinja's "applied jobs" page.

## Project structure

```
job_hunter/
├── main.py                          # Core orchestrator (bot + job loop)
├── run.py                           # Entry point: bot + remote-login server together
├── web_server.py                    # Simple web login form (alternative to mini-app)
├── job_hunter_remote_login_server.py# FastAPI remote-browser mini-app for login
├── database.py                      # SQLite layer (users, resumes, applications)
├── automator/
│   ├── base_automator.py            # Shared Playwright lifecycle + Telegram notifier
│   ├── telegram_handler.py          # Bot menus, callbacks, job start/stop
│   ├── jobinja_bot.py               # Jobinja search / apply / status sync
│   ├── jobvision_bot.py             # Jobvision search / apply / profile upload
│   └── irantalent_bot.py            # IranTalent search / apply / profile upload
├── matching/
│   ├── job_parser.py                # Job text → title / skills / technologies
│   ├── scorer.py                    # Resume-vs-job scoring engine
│   └── query_generator.py           # Resume → search keywords + categories
├── resumes/
│   ├── parser.py                    # PDF → structured ResumeProfile
│   ├── profile.py                   # Dataclasses (Experience, Education, …)
│   └── <telegram_user_id>/          # Per-user PDFs + *_state.json sessions
├── data/job_hunter.db               # SQLite database (created automatically)
├── requirements.txt
├── .env.example
└── .gitignore
```

## How it works

1. **Upload** — you send resume PDFs to the bot; they are stored under
   `resumes/<your_telegram_id>/` and registered in the database.
2. **Login** — the bot opens the remote-login mini-app; you authenticate in a
   streamed browser (CAPTCHA included). The resulting session state is saved as
   `<platform>_state.json`.
3. **Start** — the bot builds a profile per resume, generates search keywords,
   and runs each resume across each platform **sequentially**:
   - one browser per platform/resume pair, reused for every keyword;
   - the stored session is validated right before that site's work starts;
   - Jobvision uploads the profile resume once per run and stops that site's
     applications if the upload fails (to avoid applying with a stale resume).
4. **Match & apply** — each listing is parsed and scored; jobs at or above
   `min_score` are recorded as `matched`, then applied to. A job is only marked
   `applied` when a known confirmation message appears on the page; otherwise the
   status stays `unknown` so no false success is reported.
5. **Repeat** — after all resumes and platforms finish, the cycle waits 10
   minutes and starts again until you press *Stop* (cooperative stop: the current
   navigation may finish first).

## Requirements

- Python 3.11+ (tested on 3.15)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Firefox for Playwright (installed below)

## Installation

```bash
git clone <your-repo-url>
cd job-hunter

python -m venv env
# Windows:
env\Scripts\activate
# Linux/macOS:
source env/bin/activate

pip install -r requirements.txt
playwright install firefox
```

## Configuration

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from @BotFather |
| `LOGIN_WEBAPP_URL` | for login button | Base URL of the remote-login mini-app, e.g. `https://xxxx.trycloudflare.com/login` |
| `JOB_HUNTER_SESSION_DIR` | no | Where user session files live (default `resumes`) |
| `TELEGRAM_INIT_DATA_MAX_AGE` | no | Max age in seconds for Telegram Mini-App init data (default `86400`) |

> `.env`, `resumes/<id>/*_state.json`, PDF resumes, and `data/*.db` are all
> git-ignored. Never commit them: session files grant full account access.

## Usage

**Everything at once (recommended):**

```bash
python run.py
```

Starts the remote-login FastAPI server on port `8000` and the Telegram bot.

**Bot only** (no web login server):

```bash
python main.py
```

**Remote-login server only:**

```bash
uvicorn job_hunter_remote_login_server:app --host 0.0.0.0 --port 8000
```

Expose it publicly (e.g. with a Cloudflare tunnel) and put that URL in
`LOGIN_WEBAPP_URL`.

**Simple HTML login form** (alternative to the mini-app):

```bash
uvicorn web_server:app --host 0.0.0.0 --port 8000
```

Then open `http://<host>:8000/login?user_id=<telegram_user_id>`.

### Bot menu (Persian)

| Button | Action |
|---|---|
| 📥 آپلود رزومه / ➕ افزودن رزومه | Upload a PDF resume |
| 📋 مشاهده رزومه‌های من | List uploaded resumes |
| 🔑 احراز هویت و لاگین در سایت‌ها | Open the remote-login mini-app |
| 🚀 شروع ارسال خودکار رزومه | Start the apply loop |
| 🛑 توقف ارسال رزومه | Request a cooperative stop |

## Matching engine

- `resumes/parser.py` extracts sections (summary, skills, experience, projects,
  education, languages) from the PDF text.
- `matching/query_generator.py` scores resume terms and infers up to three job
  categories (backend, teaching, embedded/IoT, …), producing search keywords.
- `matching/scorer.py` scores each listing against the resume with per-profile
  weightings (English-teaching resumes weigh relevance and experience higher;
  IoT resumes weigh skills and technologies higher).

Tune thresholds in `main.py` (`min_score`, `max_pages`, `remote_only`) inside
`discover_resumes_and_build_profiles()`.

## Database

SQLite at `data/job_hunter.db` (WAL mode), created automatically. Tables:

- `users` — Telegram users
- `resumes` — uploaded PDFs per user
- `jobinja_accounts` — one row per user/platform session (legacy table name,
  used for all platforms)
- `applications` — one row per user+job URL, with status and score
- `application_events` — status-change history

Statuses: `discovered, matched, pending_approval, applied, under_review,
interview, rejected, offer, hired, withdrawn, unknown`.

## Troubleshooting

- **"Stored session is missing or invalid"** — log in again through the mini-app.
- **Login times out** — CAPTCHA/OTP probably needs a manual attempt; the visible
  browser stays open for up to 3 minutes during credential login.
- **No matches** — lower `min_score` or check the generated keywords printed as
  `[Resume] <file>: N search terms`.
- **Selectors broken after a site redesign** — update the marker tuples
  (`SUCCESS_MARKERS`, `APPLY_MARKERS`, `ALREADY_APPLIED_MARKERS`) and CSS
  selectors in the relevant `automator/*_bot.py`.

## Security notes

- Treat `*_state.json` files and `.env` as secrets.
- The remote-login server validates Telegram Mini-App init data via HMAC; if you
  expose it, keep `TELEGRAM_BOT_TOKEN` set so signatures are checked.
- This repository is configured to ignore all personal data; double-check
  `git status` before every commit.

## License

All rights reserved unless a license file is added by the author.
