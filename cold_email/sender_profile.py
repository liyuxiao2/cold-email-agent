"""Static sender identity and resume loader for candidate-outreach drafting.

The drafting stage fills a fixed template (see prompts/email_template.py); this
module supplies the deterministic sender fields and loads the full resume text
(from resume.txt) which the LLM uses to dynamically tailor introductions and
experience bullets. A static fallback experience pool is maintained for safety and tests.
"""

from dataclasses import dataclass, field
from pathlib import Path

_CURRENT_DIR = Path(__file__).resolve().parent
_RESUME_PATH = _CURRENT_DIR / "resume.txt"


def load_resume() -> str:
    if _RESUME_PATH.exists():
        return _RESUME_PATH.read_text(encoding="utf-8")
    return ""


@dataclass(frozen=True)
class SenderProfile:
    name: str
    intro: str  # first-person sentence dropped verbatim into the template
    linkedin: str
    github: str
    website: str
    resume_text: str = ""
    experience_pool: list[str] = field(default_factory=list)
    company_links: dict[str, str] = field(default_factory=dict)

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def effective_resume_text(self) -> str:
        if self.resume_text:
            return self.resume_text
        pool = "\n".join(f"- {b}" for b in self.experience_pool)
        return f"{self.intro}\n\nExperience:\n{pool}"


PROFILE = SenderProfile(
    name="Liyu Xiao",
    intro=(
        "My name is Liyu, a Computer Science student at McMaster incoming at "
        "Bot Auto as a Software Engineer, and previously at Wealthsimple and IBM."
    ),
    linkedin="https://www.linkedin.com/in/liyu-xiao-593176206/",
    github="https://github.com/liyuxiao2",
    website="https://liyuxiao.ca/",
    resume_text=load_resume(),
    experience_pool=[
        "Bot Auto: Incoming Software Engineer developing fleet navigation software.",
        "Wealthsimple: Offloaded logging to a Kafka/S3/Snowflake pipeline, cutting latency by 80% and reducing database storage from 30TB to 500GB.",
        "IBM: Built backend services, creating courses for millions of learners.",
        "Cold Email Agent: Architected an autonomous agent to research, draft, and send cold emails to 500+ companies a week.",
    ],
    company_links={
        "Wealthsimple": "https://www.wealthsimple.com/en-ca",
        "IBM": "https://www.ibm.com/ca-en",
        "Bot Auto": "https://bot.auto/",
        "Qoherent": "https://qoherent.ai/",
        "Cold Email Agent": "https://github.com/liyuxiao2/cold-email-agent",
    },
)
