"""LLM contract for candidate-outreach drafting.

The model does NOT write the whole email — the template (email_template.py) owns
structure and tone. The model returns only the *contextual* pieces via the
EmailDraftContext schema: the subject, the two company-specific interest phrases,
and a tailored selection of the sender's experience bullets. Everything else is
filled deterministically. Schema flows through the provider-agnostic generate_json
layer (Gemini response_schema / Groq field-guide).
"""

from pydantic import BaseModel, Field

RECIPIENT_TITLE = "Founder"  # startups.gallery leads are founders; no title column upstream.

EMAIL_DRAFT_SYSTEM = (
    "You fill the contextual slots of a fixed candidate-outreach email a software "
    "engineer sends to a startup founder. You do NOT write the whole email — only "
    "the fields requested.\n\n"
    "Rules:\n"
    "- `company_interest`: complete the sentence 'I'm particularly interested in ...' "
    "with a specific aspect of what THIS company builds (e.g. 'how Turo handles "
    "car-sharing marketplace technology'). Use only the provided research; never "
    "invent products or facts.\n"
    "- `admiration_detail`: complete 'I'm drawn to ...' with a concrete, non-generic "
    "detail (a technical bet, culture, or recent milestone). No flattery that could "
    "apply to any company.\n"
    "- `tailored_bullets`: choose the 3 experience bullets from the provided pool that "
    "are MOST relevant to this company, ordered most-relevant first. Return each as a "
    "'Label: achievement' string. You may lightly rephrase for relevance, but never "
    "fabricate achievements or change the numbers.\n"
    "- `subject`: a short, specific subject line referencing the company by name. No "
    "clickbait, no 'opportunity'.\n"
    "- Specific over generic throughout."
)


class EmailDraftContext(BaseModel):
    """Contextual slots the LLM fills; the template supplies everything else."""

    subject: str = Field(description="Short, company-specific subject line")
    company_interest: str = Field(description="Completes 'I'm particularly interested in ...'")
    admiration_detail: str = Field(description="Completes 'I'm drawn to ...'")
    tailored_bullets: list[str] = Field(
        description="Exactly 3 'Label: achievement' strings, most-relevant first"
    )


def build_email_draft_messages(
    founder_name: str,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
    experience_pool: list[str],
) -> str:
    """Build the drafting prompt: company research + the sender's experience pool."""
    pool = "\n".join(f"- {b}" for b in experience_pool)
    return (
        f"Company: {company_name} (recipient: {founder_name}, {RECIPIENT_TITLE})\n"
        f"What they're building / recent news: {recent_news}\n"
        f"Why their work is compelling: {hook}\n"
        f"Their tech stack: {', '.join(tech_stack)}\n\n"
        f"Sender's experience pool (choose the 3 most relevant):\n{pool}\n\n"
        "Fill the requested fields."
    )
