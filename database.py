from pathlib import Path
from datetime import datetime, timedelta, timezone
import sqlite3
import re

DB_PATH = Path(__file__).resolve().parent / "data" / "job_hunter.db"
STATUSES = {"discovered", "matched", "pending_approval", "applied", "under_review",
            "interview", "rejected", "offer", "hired", "withdrawn", "unknown"}

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class JobHunterDB:
    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def initialize(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                telegram_user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobinja_accounts(
                id INTEGER PRIMARY KEY, telegram_user_id TEXT NOT NULL, account_label TEXT,
                account_identity TEXT, session_path TEXT, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_accounts_user ON jobinja_accounts(telegram_user_id);
            CREATE TABLE IF NOT EXISTS resumes(
                id INTEGER PRIMARY KEY, telegram_user_id TEXT NOT NULL, filename TEXT NOT NULL,
                file_path TEXT NOT NULL, category TEXT, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(telegram_user_id, filename),
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS applications(
                id INTEGER PRIMARY KEY, telegram_user_id TEXT NOT NULL, jobinja_account_id INTEGER,
                resume_id INTEGER, job_url TEXT NOT NULL, job_title TEXT NOT NULL, company TEXT,
                keyword TEXT, match_score REAL, status TEXT NOT NULL DEFAULT 'discovered',
                discovered_at TEXT NOT NULL, applied_at TEXT, last_checked_at TEXT,
                next_followup_at TEXT, notes TEXT, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(telegram_user_id, job_url),
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id) ON DELETE CASCADE,
                FOREIGN KEY(jobinja_account_id) REFERENCES jobinja_accounts(id) ON DELETE SET NULL,
                FOREIGN KEY(resume_id) REFERENCES resumes(id) ON DELETE SET NULL);
            CREATE INDEX IF NOT EXISTS idx_apps_user_status ON applications(telegram_user_id,status);
            CREATE INDEX IF NOT EXISTS idx_apps_followup ON applications(next_followup_at);
            CREATE TABLE IF NOT EXISTS application_events(
                id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, old_status TEXT,
                new_status TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE);
            """)

    def ensure_user(self, user_id):
        user_id, ts = str(user_id), now()
        with self.connect() as c:
            c.execute("""INSERT INTO users VALUES(?,?,?) ON CONFLICT(telegram_user_id)
                         DO UPDATE SET updated_at=excluded.updated_at""", (user_id, ts, ts))

    def upsert_resume(self, user_id, filename, file_path, category=None, is_active=True):
        user_id, ts = str(user_id), now()
        self.ensure_user(user_id)
        with self.connect() as c:
            c.execute("""INSERT INTO resumes(telegram_user_id,filename,file_path,category,is_active,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_user_id,filename) DO UPDATE SET
                         file_path=excluded.file_path,category=excluded.category,
                         is_active=excluded.is_active,updated_at=excluded.updated_at""",
                      (user_id, filename, str(file_path), category, int(is_active), ts, ts))
            return c.execute("SELECT id FROM resumes WHERE telegram_user_id=? AND filename=?",
                             (user_id, filename)).fetchone()["id"]

    def upsert_jobinja_account(self, user_id, account_label=None, account_identity=None,
                               session_path=None, is_active=True):
        user_id, ts = str(user_id), now()
        self.ensure_user(user_id)
        with self.connect() as c:
            row = c.execute("""SELECT id FROM jobinja_accounts
                WHERE telegram_user_id=? AND account_identity=? LIMIT 1""",
                (user_id, account_identity)).fetchone() if account_identity else None
            if row:
                c.execute("""UPDATE jobinja_accounts SET account_label=?,session_path=?,is_active=?,updated_at=?
                             WHERE id=?""", (account_label, str(session_path) if session_path else None,
                                             int(is_active), ts, row["id"]))
                return row["id"]
            cur = c.execute("""INSERT INTO jobinja_accounts
                (telegram_user_id,account_label,account_identity,session_path,is_active,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""", (user_id, account_label, account_identity,
                    str(session_path) if session_path else None, int(is_active), ts, ts))
            return cur.lastrowid

    def upsert_platform_account(self, user_id, platform, session_path=None, is_active=True):
        """ثبت حساب هر پلتفرم در جدول سازگار فعلی؛ نام جدول برای سازگاری حفظ شده است."""
        platform = str(platform).strip().lower()
        if not platform:
            raise ValueError("platform is required")
        return self.upsert_jobinja_account(
            user_id,
            account_label=platform.title(),
            account_identity=platform,
            session_path=session_path,
            is_active=is_active,
        )

    def record_application(self, user_id, job_url, job_title, resume_id=None,
                           jobinja_account_id=None, company=None, keyword=None,
                           match_score=None, status="discovered", applied=False,
                           notes=None, last_error=None):
        if status not in STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        user_id, ts = str(user_id), now()
        self.ensure_user(user_id)
        applied_at = ts if applied or status == "applied" else None
        with self.connect() as c:
            c.execute("""INSERT INTO applications
                (telegram_user_id,jobinja_account_id,resume_id,job_url,job_title,company,keyword,
                 match_score,status,discovered_at,applied_at,notes,last_error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_user_id,job_url) DO UPDATE SET
                job_title=excluded.job_title, company=COALESCE(excluded.company,company),
                keyword=COALESCE(excluded.keyword,keyword), match_score=COALESCE(excluded.match_score,match_score),
                resume_id=COALESCE(excluded.resume_id,resume_id),
                jobinja_account_id=COALESCE(excluded.jobinja_account_id,jobinja_account_id),
                status=CASE WHEN status IN ('applied','under_review','interview','rejected','offer','hired','withdrawn','unknown')
                            THEN status ELSE excluded.status END,
                applied_at=COALESCE(applied_at,excluded.applied_at),
                notes=COALESCE(excluded.notes,notes),last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (user_id, jobinja_account_id, resume_id, job_url, job_title, company, keyword,
                 match_score, status, ts, applied_at, notes, last_error, ts, ts))
            app_id = c.execute("SELECT id FROM applications WHERE telegram_user_id=? AND job_url=?",
                               (user_id, job_url)).fetchone()["id"]
            current = c.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()
            event_status = current["status"] if current else status
            previous = c.execute(
                "SELECT new_status FROM application_events WHERE application_id=? ORDER BY id DESC LIMIT 1",
                (app_id,),
            ).fetchone()
            if not previous or previous["new_status"] != event_status:
                c.execute(
                    "INSERT INTO application_events(application_id,old_status,new_status,details,created_at) VALUES(?,?,?,?,?)",
                    (app_id, previous["new_status"] if previous else None, event_status,
                     notes or "Application status recorded", ts),
                )
            return app_id

    def get_application_by_url(self, user_id, job_url):
        with self.connect() as c:
            row = c.execute("SELECT * FROM applications WHERE telegram_user_id=? AND job_url=?",
                            (str(user_id), job_url)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def normalize_job_url(job_url):
        """یکسان‌سازی URL برای تطبیق لینک‌هایی که فقط در query یا اسلش تفاوت دارند."""
        from urllib.parse import urlsplit, urlunsplit
        value = (job_url or "").strip()
        if not value:
            return ""
        parts = urlsplit(value)
        path = re.sub(r"/+", "/", parts.path or "/").rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))

    def find_application_by_url(self, user_id, job_url):
        """ابتدا تطبیق دقیق و سپس تطبیق URL نرمال‌شده را انجام می‌دهد."""
        exact = self.get_application_by_url(user_id, job_url)
        if exact:
            return exact
        target = self.normalize_job_url(job_url)
        if not target:
            return None
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM applications WHERE telegram_user_id=?",
                (str(user_id),),
            ).fetchall()
        for row in rows:
            item = dict(row)
            if self.normalize_job_url(item.get("job_url")) == target:
                return item
        return None

    def update_application_info_and_status(self, user_id, application_id, company=None,
                                           new_status=None, job_url=None):
        """اطلاعات شرکت، لینک و وضعیت را بدون پاک‌کردن داده معتبر قبلی به‌روز می‌کند."""
        user_id, ts = str(user_id), now()
        if new_status is not None and new_status not in STATUSES:
            raise ValueError(f"Unsupported status: {new_status}")
        with self.connect() as c:
            row = c.execute(
                "SELECT status, company, job_url FROM applications WHERE id=? AND telegram_user_id=?",
                (application_id, user_id),
            ).fetchone()
            if not row:
                return False
            old_status = row["status"]
            final_status = new_status or old_status
            final_company = company if company and company not in ("نامشخص", "شرکت نامشخص") else row["company"]
            final_url = job_url or row["job_url"]
            c.execute("""UPDATE applications SET company=?, job_url=?, status=?,
                         last_checked_at=?, updated_at=?,
                         applied_at=CASE WHEN ? IN ('applied','under_review','interview','rejected','offer','hired')
                                         THEN COALESCE(applied_at,?) ELSE applied_at END
                         WHERE id=? AND telegram_user_id=?""",
                      (final_company, final_url, final_status, ts, ts, final_status, ts,
                       application_id, user_id))
            if new_status is not None and old_status != final_status:
                c.execute("""INSERT INTO application_events
                    (application_id,old_status,new_status,details,created_at)
                    VALUES(?,?,?,?,?)""",
                    (application_id, old_status, final_status,
                     "وضعیت از صفحه درخواست‌های ارسالی جابینجا همگام‌سازی شد.", ts))
            return True

    def update_application_status(self, user_id, application_id, new_status, details=None,
                                  next_followup_days=None):
        if new_status not in STATUSES:
            raise ValueError(f"Unsupported status: {new_status}")
        user_id, ts = str(user_id), now()
        followup = (datetime.now(timezone.utc) + timedelta(days=next_followup_days)).isoformat(timespec="seconds") if next_followup_days is not None else None
        with self.connect() as c:
            row = c.execute("SELECT status FROM applications WHERE id=? AND telegram_user_id=?",
                            (application_id, user_id)).fetchone()
            if not row:
                return False
            c.execute("""UPDATE applications SET status=?,updated_at=?,last_checked_at=?,
                         applied_at=CASE WHEN ?='applied' THEN COALESCE(applied_at,?) ELSE applied_at END,
                         next_followup_at=COALESCE(?,next_followup_at)
                         WHERE id=? AND telegram_user_id=?""",
                      (new_status, ts, ts, new_status, ts, followup, application_id, user_id))
            c.execute("""INSERT INTO application_events(application_id,old_status,new_status,details,created_at)
                         VALUES(?,?,?,?,?)""", (application_id, row["status"], new_status, details, ts))
            return True

    def list_applications(self, user_id, status=None, limit=50):
        if status and status not in STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        sql = """SELECT a.*,r.filename AS resume_filename FROM applications a
                 LEFT JOIN resumes r ON r.id=a.resume_id WHERE a.telegram_user_id=?"""
        args = [str(user_id)]
        if status:
            sql += " AND a.status=?"
            args.append(status)
        sql += " ORDER BY COALESCE(a.applied_at,a.discovered_at) DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self.connect() as c:
            return [dict(row) for row in c.execute(sql, args).fetchall()]

    def list_due_followups(self, user_id=None):
        sql = """SELECT * FROM applications WHERE next_followup_at IS NOT NULL
                 AND next_followup_at<=? AND status NOT IN ('rejected','offer','hired','withdrawn')"""
        args = [now()]
        if user_id is not None:
            sql += " AND telegram_user_id=?"
            args.append(str(user_id))
        with self.connect() as c:
            return [dict(row) for row in c.execute(sql + " ORDER BY next_followup_at", args).fetchall()]

    def get_stats(self, user_id):
        with self.connect() as c:
            rows = c.execute("SELECT status,COUNT(*) AS n FROM applications WHERE telegram_user_id=? GROUP BY status",
                             (str(user_id),)).fetchall()
        return {row["status"]: row["n"] for row in rows}
