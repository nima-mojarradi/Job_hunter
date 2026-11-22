
from dataclasses import dataclass, field
from datetime import datetime
import re
import unicodedata

from resumes.profile import ResumeProfile


@dataclass
class JobProfile:
    title: str
    description: str
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    experience_years: float | None = None


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10,
    "nov": 11, "dec": 12,
}

ALIASES = {
    "rest api": ["rest api", "rest apis", "restful api", "restful apis"],
    "django rest framework": ["django rest framework", "drf"],
    "postgresql": ["postgresql", "postgres"],
    "javascript": ["javascript", "js"],
    "c++": ["c++", "cpp"],
    "python": ["python", "پایتون"],
    "iot": ["iot", "internet of things", "اینترنت اشیا"],
}

ENGLISH_TERMS = [
    "english", "انگلیسی", "language teacher", "مدرس زبان",
    "مدرس آنلاین", "معلم زبان", "مدرس", "teacher", "teaching",
    "tutor", "instructor", "آموزش زبان", "آموزش آنلاین",
    "زبان آموز", "زبان‌آموز", "کودک و نوجوان", "online teaching",
]

TECH_TERMS = [
    "python", "django", "fastapi", "flask", "backend",
    "software engineer", "software developer", "programmer",
    "iot", "esp32", "embedded", "firmware", "arduino",
    "برنامه نویس", "برنامه‌نویس", "اینترنت اشیا",
]

TEACHING_TERMS = [
    "english teacher", "teacher", "teaching", "tutor",
    "instructor", "مدرس", "معلم", "تدریس",
    "آموزش زبان", "مدرس زبان انگلیسی",
]

ONLINE_TERMS = [
    "online", "remote", "work from home", "telecommuting",
    "دورکاری", "آنلاین", "غیرحضوری", "از راه دور",
]

REMOTE_TITLE_TERMS = [
    "دورکاری", "remote", "work from home", "online",
    "غیرحضوری", "آنلاین",
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace("-", " ").replace("–", " ").replace("—", " ")
    text = re.sub(r"[^\w\s+#.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_term(term: str, text: str) -> bool:
    term = normalize(term)
    text = normalize(text)

    if not term or not text:
        return False

    if re.search(r"[\u0600-\u06ff]", term):
        return term in text

    return bool(
        re.search(
            rf"(?<!\w){re.escape(term)}(?!\w)",
            text,
        )
    )


def skill_matches(skill: str, text: str) -> bool:
    normalized_skill = normalize(skill)
    aliases = ALIASES.get(normalized_skill, [normalized_skill])

    return any(contains_term(alias, text) for alias in aliases)


def calculate_match_score(keywords: list[str], text: str) -> float:
    keywords = list(dict.fromkeys(
        normalize(k) for k in keywords if normalize(k)
    ))

    if not keywords:
        return 0.0

    matched = sum(skill_matches(keyword, text) for keyword in keywords)
    return matched / len(keywords) * 100


def build_resume_text(resume: ResumeProfile) -> str:
    parts = [
        resume.summary or "",
        resume.name or "",
        resume.location or "",
    ]

    for category, skills in resume.skills.items():
        parts.extend([category, *skills])

    for exp in resume.experience:
        parts.extend([
            exp.title,
            exp.company,
            exp.duration,
            *exp.description,
        ])

    for project in resume.projects:
        parts.extend([
            project.name,
            *project.technologies,
            *project.description,
        ])

    for education in resume.education:
        parts.extend([
            education.degree,
            education.university,
            education.duration or "",
        ])

    for language in resume.languages:
        parts.extend([language.name, language.proficiency])

    return " ".join(str(part) for part in parts if part)


def get_skill_text(resume: ResumeProfile) -> str:
    return " ".join(
        str(skill)
        for skills in resume.skills.values()
        for skill in skills
    )


def get_experience_text(resume: ResumeProfile) -> str:
    return " ".join(
        " ".join([
            exp.title,
            exp.company,
            exp.duration,
            *exp.description,
        ])
        for exp in resume.experience
    )


def get_project_text(resume: ResumeProfile) -> str:
    return " ".join(
        " ".join([
            project.name,
            *project.technologies,
            *project.description,
        ])
        for project in resume.projects
    )


def detect_profile_type(resume: ResumeProfile) -> str:
    filename = normalize(resume.filename)

    if "english" in filename or "teacher" in filename:
        return "english"

    if "iot" in filename or "embedded" in filename:
        return "iot"

    if "python" in filename:
        return "python"

    full_text = normalize(build_resume_text(resume))
    teaching_hits = sum(
        contains_term(term, full_text)
        for term in TEACHING_TERMS
    )
    technical_hits = sum(
        contains_term(term, full_text)
        for term in TECH_TERMS
    )

    if teaching_hits >= 2 and teaching_hits > technical_hits:
        return "english"

    if any(
        contains_term(term, full_text)
        for term in ["esp32", "embedded", "firmware", "arduino"]
    ):
        return "iot"

    return "python"


def is_english_job(job: JobProfile) -> bool:
    text = normalize(" ".join([
        job.title,
        job.summary,
        job.description,
    ]))

    return any(contains_term(term, text) for term in ENGLISH_TERMS)


def is_relevant_experience(job: JobProfile, experience) -> bool:
    title = normalize(experience.title)
    description = normalize(" ".join(experience.description))
    job_text = normalize(f"{job.title} {job.description}")

    english_terms = [
        "english teacher",
        "english teaching",
        "teaching english",
        "english tutor",
        "language teacher",
        "language instructor",
        "مدرس زبان",
        "معلم زبان",
        "تدریس زبان",
        "آموزش زبان",
        "مدرس انگلیسی",
        "معلم انگلیسی",
    ]

    is_english_job = any(
        normalize(term) in job_text
        for term in english_terms
    )

    if is_english_job:
        teaching_titles = [
            "english teacher",
            "teacher",
            "tutor",
            "instructor",
            "language teacher",
            "مدرس",
            "معلم",
            "مربی",
            "استاد",
        ]

        if any(normalize(term) in title for term in teaching_titles):
            return True

        teaching_terms = [
            "teaching",
            "english",
            "lesson planning",
            "classroom management",
            "تدریس",
            "آموزش",
            "زبان انگلیسی",
            "طرح درس",
            "مدیریت کلاس",
        ]

        return sum(
            skill_matches(term, description)
            for term in teaching_terms
        ) >= 2

    relevant_titles = [
        "backend developer",
        "backend engineer",
        "software engineer",
        "software developer",
        "python developer",
        "python engineer",
        "web developer",
        "full stack developer",
        "fullstack developer",
        "iot software engineer",
        "programmer",
        "application developer",
    ]

    if any(normalize(term) in title for term in relevant_titles):
        return True

    technical_terms = [
        "python",
        "django",
        "fastapi",
        "backend",
        "software development",
        "database",
        "programming",
        "rest api",
        "microservice",
    ]

    return sum(
        skill_matches(term, description)
        for term in technical_terms
    ) >= 2



def parse_duration(duration: str, now: datetime):
    duration = str(duration or "").strip()
    duration = duration.replace("â€”", "-").replace("â€“", "-")
    duration = duration.replace("—", "-").replace("–", "-")
    duration = re.sub(r"\s*-\s*", " - ", duration)
    duration = re.sub(r"\s+", " ", duration)

    current_month = now.year * 12 + now.month - 1

    match = re.search(
        r"\b([A-Za-z]{3,9})\s+(\d{4})\s+-\s+"
        r"([A-Za-z]{3,9})\s+(\d{4})\b",
        duration, re.I
    )
    if match:
        sm, sy, em, ey = match.groups()
        start_month = MONTHS.get(sm[:3].lower())
        end_month = MONTHS.get(em[:3].lower())
        if not start_month or not end_month:
            return None
        start = int(sy) * 12 + start_month - 1
        end = int(ey) * 12 + end_month - 1
        return ("range", start, end) if end >= start else None

    match = re.search(
        r"\b([A-Za-z]{3,9})\s+(\d{4})\s+-\s+Present\b",
        duration, re.I
    )
    if match:
        month_name, year = match.groups()
        month = MONTHS.get(month_name[:3].lower())
        if not month:
            return None
        start = int(year) * 12 + month - 1
        return ("range", start, current_month) if start <= current_month else None

    match = re.search(r"\b(\d{4})\s+-\s+(\d{4})\b", duration)
    if match:
        start_year, end_year = map(int, match.groups())
        start = start_year * 12
        end = end_year * 12 + 11
        return ("range", start, end) if end >= start else None

    match = re.search(r"\b(\d{4})\s+-\s+Present\b", duration, re.I)
    if match:
        start = int(match.group(1)) * 12
        return ("range", start, current_month) if start <= current_month else None

    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(months?|mos?)\b",
        duration, re.I
    )
    if match:
        return ("duration", float(match.group(1)), 0)

    match = re.search(r"\b(\d+(?:\.\d+)?)\s*years?\b", duration, re.I)
    if match:
        return ("duration", float(match.group(1)) * 12, 0)

    return None



def calculate_relevant_experience_months(job: JobProfile, resume: ResumeProfile) -> float:
    now = datetime.now()
    ranges = []
    duration_only = 0.0

    for experience in resume.experience:
        if not is_relevant_experience(job, experience):
            continue

        parsed = parse_duration(experience.duration, now)
        if not parsed:
            continue

        if parsed[0] == "range":
            start, end = parsed[1], parsed[2]
            ranges.append((start, end))
        else:
            duration_only += parsed[1]

    ranges.sort()
    merged = []

    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end)
            )
        else:
            merged.append((start, end))

    range_months = sum(
        end - start + 1
        for start, end in merged
    )

    return range_months + duration_only



def calculate_relevance_score(
    job: JobProfile,
    resume: ResumeProfile,
) -> float:
    profile_type = detect_profile_type(resume)
    title = normalize(job.title)
    summary = normalize(resume.summary or "")
    experience_text = normalize(get_experience_text(resume))
    resume_text = normalize(build_resume_text(resume))

    if profile_type == "english":
        job_is_teaching = any(
            contains_term(term, title)
            for term in TEACHING_TERMS
        )

        experience_titles = normalize(
            " ".join(exp.title for exp in resume.experience)
        )

        teaching_experience = any(
            contains_term(term, experience_titles)
            for term in TEACHING_TERMS
        )

        title_score = (
            100.0 if job_is_teaching and teaching_experience
            else 65.0 if job_is_teaching
            else 0.0
        )

        online_score = (
            100.0 if any(
                contains_term(term, resume_text)
                for term in ONLINE_TERMS
            )
            else 0.0
        )

        english_score = (
            100.0 if contains_term("english", resume_text)
            else 0.0
        )

        return (
            title_score * 0.60
            + online_score * 0.15
            + english_score * 0.25
        )

    keywords = list(dict.fromkeys(job.skills + job.technologies))
    keyword_score = calculate_match_score(keywords, resume_text)

    title_terms = (
        ["iot", "embedded", "firmware", "esp32", "arduino"]
        if profile_type == "iot"
        else ["python", "backend", "software engineer", "developer"]
    )

    title_score = calculate_match_score(
        title_terms,
        experience_text + " " + summary,
    )

    return keyword_score * 0.8 + title_score * 0.2



def calculate_skill_score(
    job_skills: list[str],
    resume: ResumeProfile,
) -> float:
    return calculate_match_score(job_skills, get_skill_text(resume))


def calculate_technology_evidence(
    job: JobProfile,
    resume: ResumeProfile,
) -> dict:
    evidence = {}
    skill_text = get_skill_text(resume)
    experience_text = get_experience_text(resume)
    project_text = get_project_text(resume)

    for tech in job.technologies:
        locations = []

        if skill_matches(tech, skill_text):
            locations.append("skills")

        if skill_matches(tech, experience_text):
            locations.append("experience")

        if skill_matches(tech, project_text):
            locations.append("projects")

        evidence[tech] = locations

    return evidence


def calculate_technology_score(
    job_technologies: list[str],
    resume_text: str,
) -> float:
    return calculate_match_score(job_technologies, resume_text)


def calculate_project_score(
    job: JobProfile,
    resume: ResumeProfile,
) -> float:
    if not resume.projects:
        return 0.0

    keywords = list(dict.fromkeys(job.skills + job.technologies))

    if not keywords:
        return 0.0

    scores = []

    for project in resume.projects:
        text = " ".join([
            project.name,
            *project.technologies,
            *project.description,
        ])

        scores.append(calculate_match_score(keywords, text))

    scores.sort(reverse=True)
    weights = [0.6, 0.3, 0.1][:min(3, len(scores))]

    return sum(
        score * weight
        for score, weight in zip(scores[:3], weights)
    ) / sum(weights)


def calculate_summary_score(
    job: JobProfile,
    resume: ResumeProfile,
) -> float:
    if not resume.summary:
        return 0.0

    keywords = list(dict.fromkeys(job.skills + job.technologies))

    if not keywords:
        return 0.0

    return calculate_match_score(keywords, resume.summary)


def score_resume(job: JobProfile, resume: ResumeProfile) -> dict:
    profile_type = detect_profile_type(resume)
    resume_text = build_resume_text(resume)

    skills_score = calculate_skill_score(job.skills, resume)
    technologies_score = calculate_technology_score(
        job.technologies,
        resume_text,
    )

    experience_months = calculate_relevant_experience_months(job, resume)

    required_months = (
        job.experience_years * 12
        if job.experience_years is not None
        else None
    )

    if required_months is None or required_months <= 0:
        experience_score = min(experience_months / 72, 1.0) * 100
        requirement_met = None
    else:
        experience_score = min(experience_months / required_months, 1.0) * 100
        requirement_met = experience_months >= required_months

    projects_score = calculate_project_score(job, resume)
    summary_score = calculate_summary_score(job, resume)
    relevance_score = calculate_relevance_score(job, resume)

    if profile_type == "english":
        # برای مدرس زبان، تجربه و ارتباط شغلی مهم‌ترند.
        components = [
            (relevance_score, 0.45),
            (experience_score, 0.25),
            (skills_score, 0.15),
            (summary_score, 0.10),
            (technologies_score, 0.05),
        ]
    elif profile_type == "iot":
        components = [
            (skills_score, 0.25),
            (technologies_score, 0.25),
            (experience_score, 0.20),
            (projects_score, 0.15),
            (summary_score, 0.05),
            (relevance_score, 0.10),
        ]
    else:
        components = [
            (skills_score, 0.30),
            (technologies_score, 0.20),
            (experience_score, 0.20),
            (projects_score, 0.15),
            (summary_score, 0.05),
            (relevance_score, 0.10),
        ]

    total_weight = sum(
        weight for _, weight in components
    )

    total = sum(
        score * weight for score, weight in components
    ) / total_weight if total_weight else 0.0

    return {
        "filename": resume.filename,
        "profile_type": profile_type,
        "total": round(total, 2),
        "skills": round(skills_score, 2),
        "technologies": round(technologies_score, 2),
        "experience": round(experience_score, 2),
        "experience_months": round(experience_months, 1),
        "required_experience_months": (
            round(required_months, 1)
            if required_months is not None
            else None
        ),
        "experience_requirement_met": requirement_met,
        "projects": round(projects_score, 2),
        "summary": round(summary_score, 2),
        "relevance": round(relevance_score, 2),
        "technology_evidence": calculate_technology_evidence(
            job, resume
        ),
    }