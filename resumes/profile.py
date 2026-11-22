from dataclasses import dataclass, field


@dataclass
class Language:
    name: str
    proficiency: str

@dataclass
class Experience:
    title: str
    company: str
    duration: str
    description: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str
    technologies: list[str] = field(default_factory=list)
    description: list[str] = field(default_factory=list)


@dataclass
class Education:
    degree: str
    university: str
    duration: str | None = None


@dataclass
class ResumeProfile:
    filename: str

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None

    summary: str | None = None

    skills: dict[str, list[str]] = field(default_factory=dict)

    experience: list[Experience] = field(default_factory=list)

    projects: list[Project] = field(default_factory=list)

    education: list[Education] = field(default_factory=list)

    languages: list[Language] = field(default_factory=list)

    interests: list[str] = field(default_factory=list)