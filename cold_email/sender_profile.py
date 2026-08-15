"""The in-memory shape of a user's sender identity.

The dataclass survives the multi-tenant migration; only its SOURCE changed —
from a frozen module-level constant plus resume.txt in the repo, to a `profiles`
row per user (see SenderProfile.from_row).
"""

from dataclasses import dataclass, field


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

    @classmethod
    def from_row(cls, row) -> "SenderProfile":
        """Build from a `profiles` row.

        Coerces NULL JSONB columns to empty containers so callers never have to
        None-check them — the template fill would otherwise raise mid-draft.
        """
        return cls(
            name=row.name,
            intro=row.intro,
            linkedin=row.linkedin or "",
            github=row.github or "",
            website=row.website or "",
            resume_text=row.resume_text or "",
            experience_pool=list(row.experience_pool or []),
            company_links=dict(row.company_links or {}),
        )

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def effective_resume_text(self) -> str:
        """The résumé text for the drafting prompt.

        Falls back to synthesising from intro + experience_pool, which is what a
        user who filled the profile form without uploading a PDF needs.
        """
        if self.resume_text:
            return self.resume_text
        pool = "\n".join(f"- {b}" for b in self.experience_pool)
        return f"{self.intro}\n\nExperience:\n{pool}"
