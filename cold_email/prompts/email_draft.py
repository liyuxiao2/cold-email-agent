"""LLM contract for candidate-outreach drafting.

The model does NOT write the whole email — the template (email_template.py) owns
structure and tone. The model returns only the *contextual* pieces via the
EmailDraftContext schema: the subject, the two company-specific interest phrases,
a tailored introduction sentence, and a selection of experience bullets extracted
from the resume. Everything else is filled deterministically.
"""

from pydantic import BaseModel, Field

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
    "- `intro`: write a tailored first-person introduction sentence (e.g., 'My name is Liyu, a CS student at McMaster...') "
    "that highlights relevant aspects of your background (from the resume) that align with "
    "the company's domain, tech stack, or challenges. Keep it to exactly one sentence, professional, "
    "and natural-sounding.\n"
    "- `tailored_bullets`: choose or generate the 3 experience bullets from the provided resume that "
    "are MOST relevant to this company, ordered most-relevant first. Return each as a "
    "'Label: achievement' string (where Label is the company/project name, e.g., 'Wealthsimple: Developed a financial hold...'). "
    "You may lightly rephrase/tailor them for relevance, but never fabricate achievements or change the numbers/facts.\n"
    "- `subject`: a short, specific subject line referencing the company by name. No "
    "clickbait, no 'opportunity'.\n"
    "- Specific over generic throughout."
)


class EmailDraftContext(BaseModel):
    """Contextual slots the LLM fills; the template supplies everything else."""

    subject: str = Field(description="Short, company-specific subject line")
    company_interest: str = Field(description="Completes 'I'm particularly interested in ...'")
    admiration_detail: str = Field(description="Completes 'I'm drawn to ...'")
    intro: str = Field(
        description="A tailored, professional first-person introduction sentence based on the resume and company context"
    )
    tailored_bullets: list[str] = Field(
        description="Exactly 3 'Label: achievement' strings selected and tailored from the resume, ordered most-relevant first, "
    )


def build_email_draft_messages(
    recipient_name: str,
    recipient_position: str | None,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
    resume_text: str,
) -> str:
    """Build the drafting prompt: company research + the sender's full resume.

    `recipient_position` comes from company_contacts.position. It replaces a
    hardcoded "Founder" — after contact spreading, the recipient is frequently
    a CTO or a head of engineering, and telling the model otherwise produces
    copy addressed to the wrong role.
    """
    position = recipient_position or "Founder"
    return (
        f"Company: {company_name} (recipient: {recipient_name}, {position})\n"
        f"What they're building / recent news: {recent_news}\n"
        f"Why their work is compelling: {hook}\n"
        f"Their tech stack: {', '.join(tech_stack)}\n\n"
        f"Sender's Resume:\n{resume_text}\n\n"
        "Fill the requested fields."
    )
