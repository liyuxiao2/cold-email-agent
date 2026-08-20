"""LLM contract for turning résumé text into a suggested sender profile.

The result is a SUGGESTION the user reviews and edits, never an authoritative
profile. Every draft email a user sends is built from these fields, so a mangled
name or an invented link would propagate to every stranger they contact.
"""

from pydantic import BaseModel, Field

RESUME_PROFILE_SYSTEM = (
    "You extract a structured sender profile from a résumé, for a cold-outreach "
    "tool that emails startups on the candidate's behalf.\n\n"
    "Rules:\n"
    "- `name`: the candidate's full name exactly as written on the résumé.\n"
    "- `intro`: ONE first-person sentence introducing them, e.g. 'My name is "
    "Jordan, a Computer Science student at Riverdale University, previously at "
    "Acme Corp and Globex.' Professional, natural, no adjectives about their "
    "own quality.\n"
    "- `experience_pool`: 4-8 entries, each formatted EXACTLY as "
    "'Label: achievement' where Label is the company or project name. Preserve "
    "concrete numbers verbatim. Never fabricate an achievement or change a "
    "figure.\n"
    "- `company_links`: a list of {label, url} pairs, where `label` matches an "
    "experience_pool Label. Include a pair ONLY if the résumé literally contains "
    "that URL. Omit otherwise — a missing link degrades to a plain bold label, but "
    "a wrong link is sent to a stranger.\n"
    "- `linkedin`, `github`, `website`: only if present in the résumé. Null "
    "otherwise.\n"
    "- Extract, never invent. If something is absent, return null."
)


# A list of pairs rather than dict[str, str]: Pydantic renders an open-ended map
# as `additionalProperties`, which the Gemini Developer API rejects outright
# ("additionalProperties is only supported in Gemini Enterprise Agent Platform
# mode"), which failed every résumé upload with a 503. suggest_profile folds this
# back into the {label: url} dict the profile actually stores. Kept terse because
# a class docstring is sent to the model as the schema description.
class CompanyLink(BaseModel):
    """One Label -> URL pair."""

    label: str = Field(description="The experience_pool Label this URL belongs to")
    url: str = Field(description="The URL exactly as it appears in the résumé")


class ResumeProfile(BaseModel):
    """The profile fields extracted from a résumé."""

    name: str = Field(description="Full name exactly as written on the résumé")
    intro: str = Field(description="One first-person introduction sentence")
    linkedin: str | None = Field(default=None, description="LinkedIn URL if present")
    github: str | None = Field(default=None, description="GitHub URL if present")
    website: str | None = Field(default=None, description="Personal site if present")
    experience_pool: list[str] = Field(
        description="4-8 'Label: achievement' strings; the ': ' separator is required"
    )
    company_links: list[CompanyLink] = Field(
        default_factory=list, description="Label/URL pairs, only for URLs in the résumé"
    )


def build_resume_profile_prompt(resume_text: str) -> str:
    return f"Résumé:\n{resume_text}\n\nExtract the profile fields."
