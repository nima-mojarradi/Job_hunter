import re
import pymupdf

from .profile import (
    ResumeProfile,
    Experience,
    Project,
    Education,
    Language,
)


def clean_text(text: str) -> str:
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def extract_text(pdf_path: str) -> str:
    document = pymupdf.open(pdf_path)
    text = "\n".join(page.get_text() for page in document)
    document.close()
    return clean_text(text)


SKILL_SUBCATEGORIES = {
    "languages",
    "programming languages",
    "backend",
    "frontend",
    "front-end",
    "frameworks",
    "libraries",
    "embedded",
    "databases",
    "tools",
    "devops",
    "platforms",
    "technologies",
}


SECTION_ALIASES = {
    "summary": {
        "summary",
        "professional summary",
        "profile",
        "professional profile",
        "career summary",
        "career profile",
        "about me",
        "objective",
    },
    "experience": {
        "experience",
        "professional experience",
        "work experience",
        "employment history",
        "work history",
        "professional background",
        "teaching experience",
    },
    "skills": {
        "skills",
        "technical skills",
        "technical expertise",
        "core skills",
        "key skills",
    },
    "projects": {
        "projects",
        "selected projects",
        "personal projects",
        "academic projects",
        "key projects",
    },
    "education": {
        "education",
        "academic background",
        "educational background",
    },
    "languages": {
        "languages",
        "language skills",
    },
    "interests": {
        "interests",
        "areas of interest",
        "professional interests",
    },
}


def normalize_heading(line: str) -> str:
    line = line.strip().lower()
    line = re.sub(r"^[•*#§ï+]+", "", line)
    line = re.sub(r"[•*#§ï+]+$", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def detect_sections(text: str) -> dict[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sections = {}
    current_section = None
    current_lines = []

    normalized_aliases = {
        section: {normalize_heading(alias) for alias in aliases}
        for section, aliases in SECTION_ALIASES.items()
    }

    def save_section():
        if current_section is not None:
            sections[current_section] = "\n".join(current_lines)

    for line in lines:
        normalized = normalize_heading(line)
        detected_section = None

        for section_name, aliases in normalized_aliases.items():
            if normalized in aliases:
                detected_section = section_name
                break

        if current_section == "skills" and normalized in SKILL_SUBCATEGORIES:
            detected_section = None

        if detected_section is not None:
            save_section()
            current_section = detected_section
            current_lines = []
        elif current_section is not None:
            current_lines.append(line)

    save_section()

    return sections


def parse_basic_info(text: str) -> dict:
    data = {
        "name": None,
        "email": None,
        "phone": None,
        "location": None,
    }

    email_match = re.search(
        r"[\w\.-]+@[\w\.-]+\.\w+",
        text,
    )

    if email_match:
        data["email"] = email_match.group(0)

    phone_match = re.search(
        r"\+?98[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4}",
        text,
    )

    if phone_match:
        data["phone"] = phone_match.group(0)

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if lines:
        data["name"] = lines[0]

    for line in lines:
        if "Bojnourd" in line or "Bojnord" in line:
            match = re.search(
                r"(Bojnou?rd|Bojnord),?\s*Iran",
                line,
                re.IGNORECASE,
            )

            if match:
                data["location"] = match.group(0)
                break

    if data["location"] is None:
        for line in lines:
            if re.search(r"\bIran\b", line, re.IGNORECASE):
                if "Bojnord" in line or "Bojnourd" in line:
                    data["location"] = line
                    break

    return data


def parse_summary(section_text: str) -> str | None:
    if not section_text:
        return None

    summary = " ".join(
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    )

    return summary if summary else None


def parse_skills(section_text: str) -> dict[str, list[str]]:
    if not section_text:
        return {}

    skills = {}
    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    current_category = "general"
    skills.setdefault(current_category, [])

    for line in lines:
        line = line.lstrip("•-*").strip()
        if not line:
            continue

        if ":" in line:
            category, values = line.split(":", 1)
            current_category = category.strip().lower()
            skills.setdefault(current_category, [])
            
            if values.strip():
                items = [
                    item.strip()
                    for item in re.split(r",|;", values)
                    if item.strip()
                ]
                skills[current_category].extend(items)
            continue

        if line.lower() in SKILL_SUBCATEGORIES:
            current_category = line.lower()
            skills.setdefault(current_category, [])
            continue

        items = [
            item.strip()
            for item in re.split(r",|;", line)
            if item.strip()
        ]
        skills[current_category].extend(items)

    return {k: v for k, v in skills.items() if v}


def is_bullet(line: str) -> bool:
    return line.strip().startswith(("•", "-", "*"))


def clean_bullet(line: str) -> str:
    return line.lstrip("•-* ").strip()


def is_duration(line: str) -> bool:
    pattern = re.compile(
        r"(?i)^("
        r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}"
        r"\s*(?:[–-]\s*)?"
        r"(?:present|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}))"
        r"|(?:\d{4}\s*(?:[–-]\s*)?(?:present|\d{4}))"
        r"|(?:\d+\s+(?:month|months|year|years))"
        r")$"
    )

    return bool(pattern.match(line.strip()))


def split_title_company(line: str) -> tuple[str, str]:
    line = line.strip()

    separators = [" — ", " – ", " - ", " | "]
    for separator in separators:
        if separator in line:
            title, company = line.split(separator, 1)
            return title.strip(), company.strip()

    return line, ""


def is_experience_start(lines: list[str], index: int) -> bool:
    if index >= len(lines) or is_bullet(lines[index]):
        return False

    for j in range(index + 1, min(index + 5, len(lines))):
        if is_duration(lines[j]):
            return True

    return False


def parse_experience(section_text: str) -> list[Experience]:
    if not section_text:
        return []

    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    experiences = []
    i = 0

    location_pattern = re.compile(
        r"^[A-Za-z .'-]+,\s*Iran$",
        re.IGNORECASE
    )

    while i < len(lines):
        if is_bullet(lines[i]):
            i += 1
            continue

        title = lines[i]
        i += 1

        title, inline_company = split_title_company(title)

        if title.lower().endswith(" remote"):
            title = title[:-7].strip()

        company_parts = []

        if inline_company:
            inline_company = re.sub(
                r"\s+[A-Za-z]+,\s*Iran$",
                "",
                inline_company,
                flags=re.IGNORECASE
            ).strip()

            if inline_company:
                company_parts.append(inline_company)

        duration = ""

        while i < len(lines) and not is_bullet(lines[i]):
            current = lines[i]

            if is_duration(current):
                duration = current
                i += 1
                break

            if location_pattern.fullmatch(current):
                i += 1
                continue

            company_parts.append(current)
            i += 1

        while i < len(lines) and not is_bullet(lines[i]):
            current = lines[i]

            if is_experience_start(lines, i):
                break

            if location_pattern.fullmatch(current):
                i += 1
                continue

            if current.lower() == "remote":
                i += 1
                continue

            company_parts.append(current)
            i += 1

        description = []

        while i < len(lines):
            current = lines[i]

            if is_bullet(current):
                description.append(clean_bullet(current))
                i += 1
                continue

            if is_experience_start(lines, i):
                break

            if description:
                description[-1] += " " + current
                i += 1
                continue

            break

        company = " - ".join(
            part.strip()
            for part in company_parts
            if part.strip()
        )

        experiences.append(
            Experience(
                title=title,
                company=company,
                duration=duration,
                description=description
            )
        )

    return experiences


def parse_project_technology_line(line: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"\s*[—–]\s*", line)
        if item.strip()
    ]


KNOWN_TECHNOLOGIES = {
    "Python": ["Python"],
    "Django": ["Django"],
    "Django REST Framework": ["Django REST Framework", "DRF"],
    "FastAPI": ["FastAPI"],
    "Flask": ["Flask"],
    "JavaScript": ["JavaScript"],
    "TypeScript": ["TypeScript"],
    "C++": ["C++"],
    "C#": ["C#"],
    "Java": ["Java"],
    "PHP": ["PHP"],
    "Node.js": ["Node.js", "NodeJS"],
    "React": ["React"],
    "Vue.js": ["Vue.js", "Vue"],
    "REST API": ["REST API", "RESTful API", "REST APIs"],
    "PostgreSQL": ["PostgreSQL", "Postgres"],
    "MySQL": ["MySQL"],
    "SQLite": ["SQLite"],
    "MongoDB": ["MongoDB"],
    "Redis": ["Redis"],
    "Celery": ["Celery"],
    "RabbitMQ": ["RabbitMQ"],
    "Docker": ["Docker"],
    "Git": ["Git"],
    "ESP32": ["ESP32"],
    "Arduino": ["Arduino"],
    "MQTT": ["MQTT"],
    "Telegram": ["Telegram"],
    "HTML": ["HTML"],
    "CSS": ["CSS"],
    "Linux": ["Linux"],
    "GitHub": ["GitHub"],
}


def extract_known_technologies(text: str) -> list[str]:
    technologies = []

    for technology, aliases in KNOWN_TECHNOLOGIES.items():
        for alias in aliases:
            pattern = (
                r"(?<!\w)"
                + re.escape(alias)
                + r"(?!\w)"
            )

            if re.search(pattern, text, re.IGNORECASE):
                technologies.append(technology)
                break

    return technologies


def parse_inline_project(line: str) -> Project:
    content = clean_bullet(line)

    if ":" not in content:
        return Project(
            name=content,
            technologies=extract_known_technologies(content),
            description=[],
        )

    name, remaining = content.split(":", 1)
    name = name.strip()
    remaining = remaining.strip()

    parts = re.split(r"\s+[—–]\s+", remaining, maxsplit=1)

    technologies = []
    description = []

    if len(parts) == 2:
        source, desc = parts
        desc = desc.strip()

        if source.strip().lower() != "github":
            technologies.extend(
                parse_project_technology_line(source)
            )

        description.append(desc)
    elif remaining:
        description.append(remaining)

    detected = extract_known_technologies(
        name + " " + " ".join(description)
    )

    for technology in detected:
        if technology not in technologies:
            technologies.append(technology)

    return Project(
        name=name,
        technologies=technologies,
        description=description,
    )


def is_project_start(lines: list[str], index: int) -> bool:
    if index >= len(lines) or is_bullet(lines[index]):
        return False

    if index + 1 >= len(lines):
        return False

    next_line = lines[index + 1]

    return (
        not is_bullet(next_line)
        and ("—" in next_line or "–" in next_line)
    )


def parse_projects(section_text: str) -> list[Project]:
    if not section_text:
        return []

    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    if not lines:
        return []

    projects = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if is_bullet(line) and ":" in line:
            projects.append(parse_inline_project(line))
            i += 1
            continue

        if is_bullet(line):
            i += 1
            continue

        project_name = line
        i += 1

        technologies = []

        if i < len(lines):
            technology_line = lines[i]

            if (
                not is_bullet(technology_line)
                and ("—" in technology_line or "–" in technology_line)
            ):
                technologies = parse_project_technology_line(
                    technology_line
                )
                i += 1

        description = []

        while i < len(lines):
            current = lines[i]

            if is_bullet(current):
                description.append(clean_bullet(current))
                i += 1
                continue

            if is_project_start(lines, i):
                break

            if description:
                description[-1] += " " + current
                i += 1
                continue

            break

        projects.append(
            Project(
                name=project_name,
                technologies=technologies,
                description=description,
            )
        )

    return projects


def parse_education(section_text: str) -> list[Education]:
    if not section_text:
        return []

    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    education = []
    i = 0

    while i < len(lines):
        degree = lines[i]
        institution = ""
        duration = ""
        i += 1

        if i < len(lines):
            next_line = lines[i]
            if re.search(r"(19|20)\d{2}", next_line) or is_duration(next_line):
                duration = next_line
                i += 1
            else:
                institution = next_line
                i += 1

        if not duration and i < len(lines):
            next_line = lines[i]
            if re.search(r"(19|20)\d{2}", next_line) or is_duration(next_line):
                duration = next_line
                i += 1
            elif not institution:
                institution = next_line
                i += 1

        if not institution and i < len(lines):
            next_line = lines[i]
            if not re.search(r"(19|20)\d{2}", next_line):
                institution = next_line
                i += 1

        education.append(
            Education(
                degree=degree,
                duration=duration,
                university=institution,
            )
        )

    return education


def parse_languages(section_text: str) -> list[Language]:
    if not section_text:
        return []

    languages = []

    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    for line in lines:
        line = clean_bullet(line)

        if ":" in line:
            name, proficiency = line.split(":", 1)

            languages.append(
                Language(
                    name=name.strip(),
                    proficiency=proficiency.strip(),
                )
            )

    return languages


def parse_resume(pdf_path: str) -> ResumeProfile:
    text = extract_text(pdf_path)

    sections = detect_sections(text)
    basic_info = parse_basic_info(text)

    return ResumeProfile(
        filename=pdf_path,
        name=basic_info["name"],
        email=basic_info["email"],
        phone=basic_info["phone"],
        location=basic_info["location"],
        summary=parse_summary(
            sections.get("summary", "")
        ),
        skills=parse_skills(
            sections.get("skills", "")
        ),
        experience=parse_experience(
            sections.get("experience", "")
        ),
        projects=parse_projects(
            sections.get("projects", "")
        ),
        education=parse_education(
            sections.get("education", "")
        ),
        languages=parse_languages(
            sections.get("languages", "")
        ),
        interests=[],
    )