import re
from collections import Counter, defaultdict

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "had", "are", "was", "were", "will", "can", "you", "your", "our",
    "their", "they", "them", "his", "her", "she", "him", "its", "into",
    "using", "use", "used", "work", "working", "experience", "years",
    "year", "skills", "skill", "responsible", "including", "etc",
    "about", "over", "under", "more", "less", "other", "also", "based",
    "good", "strong", "ability", "knowledge", "professional", "team",
    "teams", "project", "projects", "company", "university", "education",
    "degree", "bachelor", "master", "native", "fluent", "intermediate",
    "beginner", "advanced", "language", "languages", "english", "persian",
    "iranian", "remote", "online", "in-person", "available", "present",
    "current", "currenty", "from", "to", "at", "in", "on", "of", "as",
    "a", "an", "is", "it", "or", "be", "by", "we", "i", "me", "my",
    "our", "not", "but", "if", "all", "any", "both", "each", "per",
    "همکاری", "توانایی", "مهارت", "مهارتها", "سابقه", "تجربه",
    "مسلط", "آشنا", "آشنایی", "فعالیت", "شرکت", "دانشگاه", "کار",
    "کاری", "پروژه", "پروژهها", "و", "در", "از", "به", "با", "برای",
    "است", "میباشد", "دارای", "همراه", "مختلف", "مناسب", "سایر"
}

# General job-family rules. These are not tied to specific resume filenames.
CATEGORY_RULES = {
    "backend": {
        "terms": {
            "python", "django", "fastapi", "flask", "backend", "back-end",
            "rest", "api", "apis", "database", "postgresql", "mysql",
            "mongodb", "redis", "celery", "rabbitmq", "microservice",
            "microservices", "دیتابیس", "بک اند", "برنامه نویس بک اند"
        },
        "queries": ["Backend Developer", "برنامه نویس بک اند"]
    },
    "frontend": {
        "terms": {
            "frontend", "front-end", "react", "next.js", "nextjs",
            "javascript", "typescript", "html", "css", "vue", "angular",
            "فرانت اند", "برنامه نویس فرانت اند"
        },
        "queries": ["Frontend Developer", "برنامه نویس فرانت اند"]
    },
    "fullstack": {
        "terms": {"fullstack", "full-stack", "full stack"},
        "queries": ["Full Stack Developer", "برنامه نویس فول استک"]
    },
    "embedded_iot": {
        "terms": {
            "esp32", "esp8266", "embedded", "iot", "mqtt", "arduino",
            "firmware", "microcontroller", "lvgl", "freertos", "twai",
            "canbus", "w5500", "electronics", "embedded c", "embedded c++",
            "اینترنت اشیا", "سیستم نهفته", "امبدد", "میکروکنترلر"
        },
        "queries": ["Embedded Developer", "برنامه نویس امبدد", "کارشناس اینترنت اشیا"]
    },
    "teaching": {
        "terms": {
            "teacher", "teaching", "tutor", "instructor", "educator",
            "classroom", "lesson", "lessons", "grammar", "vocabulary",
            "students", "student", "school", "ielts", "tefl", "tesol",
            "مدرس", "معلم", "آموزش", "تدریس", "زبان"
        },
        "queries": ["مدرس زبان انگلیسی", "English Teacher", "مدرس زبان"]
    },
    "data": {
        "terms": {
            "data", "analytics", "analyst", "analysis", "pandas", "numpy",
            "statistics", "scikit-learn", "machine-learning", "machine",
            "learning", "tensorflow", "pytorch", "powerbi", "tableau",
            "داده", "تحلیلگر داده", "علم داده"
        },
        "queries": ["Data Analyst", "تحلیلگر داده", "Data Scientist"]
    },
    "devops": {
        "terms": {
            "devops", "docker", "kubernetes", "linux", "nginx", "aws",
            "azure", "ci/cd", "deployment", "infrastructure", "terraform",
            "لینوکس", "دواپس"
        },
        "queries": ["DevOps Engineer", "کارشناس دواپس"]
    },
    "network": {
        "terms": {
            "network", "networking", "routing", "switching", "ccna",
            "dhcp", "dns", "tcp/ip", "firewall", "pf", "ipfw",
            "شبکه", "کارشناس شبکه"
        },
        "queries": ["Network Engineer", "کارشناس شبکه"]
    },
    "mobile": {
        "terms": {
            "android", "kotlin", "swift", "flutter", "dart", "ios",
            "mobile", "اپلیکیشن", "اندروید", "موبایل"
        },
        "queries": ["Mobile Developer", "برنامه نویس اندروید"]
    }
}

def clean_text(text):
    text = str(text or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[•●▪►]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def tokenize(text):
    text = clean_text(text)
    return re.findall(r"[a-zA-Z][a-zA-Z0-9+#./_-]*|[\u0600-\u06FF]+", text)

def add_phrase(counter, phrase, weight):
    phrase = clean_text(phrase).strip(" .,:;|-/")
    if not phrase or len(phrase) > 65:
        return

    words = tokenize(phrase)
    if not words:
        return

    # Avoid importing an entire sentence as a search keyword.
    if len(words) > 5:
        return

    meaningful = [
        word for word in words
        if word not in STOPWORDS and len(word) > 1
    ]

    if not meaningful:
        return

    normalized = " ".join(meaningful)
    counter[normalized] += weight

    # Individual terms are useful, but phrases receive more weight.
    if len(meaningful) > 1:
        for word in meaningful:
            counter[word] += weight * 0.35

def collect_resume_terms(resume_text, resume_data):
    scores = Counter()
    text = clean_text(resume_text)

    # Full-resume term frequency: a baseline, not the whole algorithm.
    tokens = [
        token for token in tokenize(text)
        if token not in STOPWORDS and len(token) > 1
    ]
    frequency = Counter(tokens)

    for term, count in frequency.items():
        scores[term] += min(count, 8) * 0.5

    # Give extra importance to text in meaningful resume sections.
    section_weights = [
        (r"(?is)(summary|profile|objective|خلاصه|معرفی)(.*?)(?=\n[A-Z][A-Z /&-]{2,}\n|\Z)", 2.5),
        (r"(?is)(experience|employment|سابقه کاری|تجربه کاری)(.*?)(?=\n[A-Z][A-Z /&-]{2,}\n|\Z)", 2.0),
        (r"(?is)(skills|technical skills|مهارتها|مهارت ها)(.*?)(?=\n[A-Z][A-Z /&-]{2,}\n|\Z)", 2.5),
        (r"(?is)(projects|پروژه ها|پروژهها)(.*?)(?=\n[A-Z][A-Z /&-]{2,}\n|\Z)", 2.2),
    ]

    for pattern, weight in section_weights:
        for match in re.finditer(pattern, text):
            for token in tokenize(match.group(2)):
                if token not in STOPWORDS and len(token) > 1:
                    scores[token] += weight

    # Structured information is more reliable than accidental repetitions.
    skills = getattr(resume_data, "skills", {}) or {}
    if isinstance(skills, dict):
        for category, items in skills.items():
            add_phrase(scores, category, 1.5)
            if isinstance(items, (list, tuple, set)):
                for item in items:
                    add_phrase(scores, item, 4.0)
            elif isinstance(items, str):
                add_phrase(scores, items, 3.0)

    for project in getattr(resume_data, "projects", []) or []:
        add_phrase(scores, getattr(project, "title", ""), 4.5)
        add_phrase(scores, getattr(project, "name", ""), 4.5)
        add_phrase(scores, getattr(project, "description", ""), 1.5)

        technologies = getattr(project, "technologies", []) or []
        if isinstance(technologies, str):
            technologies = [technologies]

        for technology in technologies:
            add_phrase(scores, technology, 3.5)

    for experience in getattr(resume_data, "experience", []) or []:
        add_phrase(scores, getattr(experience, "title", ""), 5.0)
        add_phrase(scores, getattr(experience, "position", ""), 5.0)
        add_phrase(scores, getattr(experience, "role", ""), 5.0)
        add_phrase(scores, getattr(experience, "description", ""), 1.5)
        add_phrase(scores, getattr(experience, "company", ""), 0.5)

    summary = getattr(resume_data, "summary", "")
    add_phrase(scores, summary, 2.5)

    # Remove terms that are unlikely to be useful for job search.
    filtered = Counter()
    for term, score in scores.items():
        words = term.split()
        if not words or len(term) < 2 or len(term) > 60:
            continue
        if all(word in STOPWORDS for word in words):
            continue
        if re.fullmatch(r"[\d./-]+", term):
            continue
        filtered[term] = score

    return filtered

def infer_categories(term_scores):
    category_scores = defaultdict(float)
    generic_terms = {
        "web", "software", "communication", "management", "device",
        "development", "developer", "project", "technology", "system",
        "systems", "application", "work", "team", "experience"
    }

    for category, rule in CATEGORY_RULES.items():
        matched_terms = set()

        for term, score in term_scores.items():
            normalized = term.casefold().strip()
            if not normalized or normalized in generic_terms:
                continue

            for known_term in rule["terms"]:
                known = known_term.casefold().strip()

                if normalized == known:
                    matched_terms.add(normalized)
                    category_scores[category] += score * 2.0
                    break

                if len(known) >= 4 and (known in normalized or normalized in known):
                    matched_terms.add(normalized)
                    category_scores[category] += score * 0.45
                    break

        category_scores[category] += min(len(matched_terms), 6) * 1.5

    ranked = sorted(category_scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return []

    highest = ranked[0][1]
    return [
        category for category, score in ranked
        if score >= max(4.0, highest * 0.40)
    ][:3]

def generate_search_queries(resume_text, resume_data, max_queries=7):
    term_scores = collect_resume_terms(resume_text, resume_data)
    categories = infer_categories(term_scores)
    queries = []
    seen = set()

    generic_query_terms = {
        "private", "interactive", "communication", "management", "device",
        "web", "software", "developer", "development", "plans", "planning",
        "improving", "student", "students", "lesson", "project", "projects",
        "github", "telegram", "bot", "fingerprint", "framework", "arduino"
    }

    def add_query(query):
        query = re.sub(r"\s+", " ", str(query or "")).strip(" ,.;:/-")
        key = query.casefold()
        if not query or key in seen or len(query) > 70 or len(query.split()) > 5:
            return
        if all(part.casefold() in generic_query_terms for part in query.split()):
            return
        seen.add(key)
        queries.append(query)

    # Role-oriented queries first.
    for category in categories:
        for query in CATEGORY_RULES[category]["queries"]:
            add_query(query)

    ranked_terms = sorted(term_scores.items(), key=lambda item: item[1], reverse=True)

    # Add useful multiword phrases from the resume, but avoid project names and generic noise.
    for term, score in ranked_terms:
        if len(queries) >= max_queries:
            break
        words = term.split()
        if score < 3.0 or len(words) < 2:
            continue
        if any(word in generic_query_terms for word in words):
            continue
        if any(word in STOPWORDS for word in words):
            continue
        add_query(term)

    # Only use individual terms as a fallback if too few phrase queries exist.
    for term, score in ranked_terms:
        if len(queries) >= max_queries:
            break
        if score < 5.0 or len(term.split()) != 1 or term in generic_query_terms:
            continue
        if term in STOPWORDS or len(term) < 3:
            continue
        add_query(term)

    return {
        "categories": categories,
        "queries": queries[:max_queries],
        "top_terms": ranked_terms[:15],
    }