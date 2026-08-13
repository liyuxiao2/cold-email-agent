"""Static sender identity for candidate-outreach drafting.

The drafting stage fills a fixed template (see prompts/email_template.py); this
module supplies the deterministic sender fields and the *pool* of achievement
bullets the LLM tailors from per company. Expand EXPERIENCE_POOL with more
bullets to give the model richer material to select from.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SenderProfile:
    name: str
    intro: str  # first-person sentence dropped verbatim into the template
    linkedin: str
    github: str
    experience_pool: list[str] = field(default_factory=list)

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


PROFILE = SenderProfile(
    name="Liyu Xiao",
    intro=(
        "My name is Liyu, a Computer Science student at McMaster currently "
        "interning at Wealthsimple and previously at IBM."
    ),
    linkedin="https://www.linkedin.com/in/liyu-xiao-593176206/",
    github="https://github.com/liyuxiao2",
    experience_pool=[
        "Wealthsimple: Architected a Ruby adapter and led development of an RESP "
        "engine for 3M+ clients.",
        "IBM: Built backend infrastructure for 25,000+ authors, cutting database "
        "cluster calls by 66%.",
        "DevOps/Data: Reduced CI job startup times by 60% and optimized data "
        "pipelines to process 2,700+ recordings.",
    ],
)
