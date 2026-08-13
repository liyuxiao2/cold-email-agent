from pydantic import BaseModel, Field

from cold_email.config import settings

EMAIL_DRAFT_SYSTEM = (
    "You write cold emails for software engineers reaching out to potential employers. "
    "The emails are peer-to-peer, specific, and short. You never use filler openers. "
    "You always reference something specific from research. "
    "You always end with one clear ask.\n\n"
    "Rules:\n"
    "- No 'I hope this email finds you well' or similar openers\n"
    "- First sentence must reference a specific detail from the research\n"
    "- Body ≤ 150 words total\n"
    "- One ask in the final sentence only\n"
    "- Tone: confident peer, not job applicant\n"
    "- Do not mention 'internship' or 'opportunity' — just propose a conversation"
)

class EmailDraft(BaseModel):
    """Generated cold email — used as the Gemini response_schema."""

    subject: str = Field(description="Email subject line")
    body: str = Field(description="Email body, ≤ 150 words")


# startups.gallery leads are founders and there's no title column upstream.
RECIPIENT_TITLE = "Founder"


def build_email_draft_messages(
    founder_name: str,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
) -> str:
    """Build the drafting prompt. Sender identity comes from settings and the
    recipient title is defaulted, so callers only pass the recipient/research
    fields."""
    return (
        f"Sender: {settings.sender_name}, {settings.sender_role} "
        f"at {settings.sender_company}\n"
        f"Recipient: {founder_name}, {RECIPIENT_TITLE} at {company_name}\n"
        f"Tech stack: {', '.join(tech_stack)}\n"
        f"Recent news: {recent_news}\n"
        f"Hook: {hook}\n\n"
        "Write the subject line and email body."
    )
