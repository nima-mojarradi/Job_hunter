
import re
from matching.scorer import JobProfile

KNOWN_SKILLS = [
    "Python", "Django", "Django REST Framework", "FastAPI", "Flask",
    "PostgreSQL", "MySQL", "MongoDB", "Docker", "REST API",
    "JavaScript", "TypeScript", "React", "Next.js", "HTML", "CSS",
    "C++", "Java", "Go",
    "English Teaching", "Teaching English", "English Language",
    "Teaching", "Classroom Management", "Lesson Planning",
    "IELTS", "TOEFL", "TTC", "CELTA", "Translation",
]

KNOWN_TECHNOLOGIES = [
    "Celery", "Redis", "RabbitMQ", "Kafka", "Elasticsearch",
    "Nginx", "AWS", "Azure", "Git", "GitHub", "Linux",
]

JOB_TITLE_PATTERNS = [
    r"استخدام\s+(.+?)(?=\n|$)",
    r"(?:عنوان شغلی|عنوان موقعیت شغلی)\s*[:：]?\s*(.+?)(?=\n|$)",
    r"((?:مدرس|معلم|مربی|استاد)\s+.{0,60})",
    r"((?:مترجم|ویراستار|نویسنده)\s+.{0,60})",
    r"((?:English\s+)?(?:Teacher|Tutor|Instructor|Translator|Content Editor))",
    r"((?:Senior\s+|Junior\s+)?(?:Developer|Engineer|Programmer|Designer|Analyst|Manager|Administrator|Specialist))",
]

GENERIC_TITLES = (
    "jobinja", "جابینجا", "استخدام، آگهی استخدام",
    "جستجوگر حرفه ای استخدام",
)

SKILL_ALIASES = {
    "Django": ["Django", "جنگو"],
    "FastAPI": ["FastAPI", "فست آپی", "فست‌آپی"],
    "Flask": ["Flask", "فلاسک"],
    "Python": ["Python", "پایتون"],
    "Django REST Framework": ["Django REST Framework", "DRF"],
    "REST API": ["REST API", "RESTful API", "RESTful APIs", "سرویس رست"],
    "PostgreSQL": ["PostgreSQL", "Postgres", "پستگرس"],
    "MongoDB": ["MongoDB", "مونگو"],
    "Docker": ["Docker", "داکر"],
    "English Teaching": [
        "English teaching", "teaching English", "English teacher",
        "مدرس زبان انگلیسی", "معلم زبان انگلیسی", "تدریس زبان انگلیسی",
        "تدریس انگلیسی", "آموزش زبان انگلیسی",
    ],
    "Teaching": [
        "teaching", "teacher", "tutor", "instructor",
        "تدریس", "آموزش", "معلم", "مدرس", "مربی",
    ],
    "English Language": [
        "English language", "English proficiency",
        "زبان انگلیسی", "تسلط به زبان انگلیسی",
    ],
    "Classroom Management": [
        "classroom management", "مدیریت کلاس",
    ],
    "Lesson Planning": [
        "lesson planning", "lesson plans",
        "برنامه‌ریزی درسی", "طرح درس",
    ],
    "IELTS": ["IELTS", "آیلتس"],
    "TOEFL": ["TOEFL", "تافل"],
    "TTC": ["TTC"],
    "CELTA": ["CELTA"],
    "Translation": ["translation", "translator", "ترجمه", "مترجم"],
}

TECH_ALIASES = {
    "Celery": ["Celery", "سلری"],
    "Redis": ["Redis", "ردیس"],
    "RabbitMQ": ["RabbitMQ", "رابت‌ام‌کیو"],
    "Elasticsearch": ["Elasticsearch", "الاستیک سرچ"],
    "Nginx": ["Nginx", "انجینکس"],
    "Docker": ["Docker", "داکر"],
    "Linux": ["Linux", "لینوکس"],
}


def normalize_text(text: str) -> str:
    text = (text or "").replace("\u200c", " ")
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_title(text: str) -> str:
    lines = [
        normalize_text(line)
        for line in (text or "").splitlines()
        if normalize_text(line)
    ]

    for line in lines[:35]:
        if any(generic in line.casefold() for generic in GENERIC_TITLES):
            continue

        for pattern in JOB_TITLE_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                title = match.group(1).strip(" \t:：-–|")
                if title and not any(
                    generic in title.casefold()
                    for generic in GENERIC_TITLES
                ):
                    return title

    return lines[0] if lines else ""


def _contains_alias(text: str, candidate: str) -> bool:
    if re.search(r"[\u0600-\u06FF]", candidate):
        return candidate.casefold() in text.casefold()

    return bool(
        re.search(
            rf"(?<!\w){re.escape(candidate)}(?!\w)",
            text,
            re.IGNORECASE,
        )
    )


def extract_skills(text: str) -> list[str]:
    text = normalize_text(text)
    found = []

    for skill in KNOWN_SKILLS:
        candidates = SKILL_ALIASES.get(skill, [skill])
        if any(_contains_alias(text, item) for item in candidates):
            found.append(skill)

    return found


def extract_technologies(text: str) -> list[str]:
    text = normalize_text(text)
    found = []

    for technology in KNOWN_TECHNOLOGIES:
        candidates = TECH_ALIASES.get(technology, [technology])
        if any(_contains_alias(text, item) for item in candidates):
            found.append(technology)

    return found


def extract_experience_years(text: str) -> float | None:
    patterns = [
        r"(\d+(?:\.\d+)?)\+?\s*years?\s+of\s+[\w\s-]*?experience",
        r"(\d+(?:\.\d+)?)\+?\s*years?\s+experience",
        r"experience\s*(?:of\s*)?(\d+(?:\.\d+)?)\+?\s*years?",
        r"(\d+(?:\.\d+)?)\s*سال\s*(?:سابقه|تجربه)",
        r"(?:حداقل\s*)?(\d+(?:\.\d+)?)\s*سال\s*(?:سابقه کار|تجربه کاری)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return float(match.group(1))

    return None


def parse_job(text: str) -> JobProfile:
    cleaned_text = normalize_text(text)
    title = extract_title(cleaned_text)

    return JobProfile(
        title=title,
        description=cleaned_text,
        skills=extract_skills(cleaned_text),
        technologies=extract_technologies(cleaned_text),
        experience_years=extract_experience_years(cleaned_text),
    )
