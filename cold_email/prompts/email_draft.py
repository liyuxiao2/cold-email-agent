from pydantic import BaseModel, Field

from cold_email.config import settings

EMAIL_DRAFT_SYSTEM = (
    "You write short cold emails from a software engineer to a startup founder. "
    "The point is to convey genuine, specific interest in what THE COMPANY is "
    "building — not to pitch the sender. It should read like it's from someone who "
    "is authentically excited about their work and wants to be part of it.\n\n"
    "Rules:\n"
    "- Open on a specific, concrete detail about the company's work (a recent raise, "
    "launch, or technical bet). No filler openers ('I hope this finds you well').\n"
    "- Lead with WHY their work is compelling to the sender — curiosity about what "
    "they're building, not the sender's skills.\n"
    "- Do NOT pitch the sender's experience, list qualifications, or say what they "
    "could 'add', 'help with', or 'bring'. At most one brief, natural mention of "
    "relevant background — never the focus, never a value proposition.\n"
    "- Specific over generic. No buzzwords, no flattery that could apply to any "
    "company. Only use facts from the provided research; never invent details.\n"
    "- Body ≤ 120 words.\n"
    "- Tone: a sharp peer who genuinely admires the work, not a job applicant.\n"
    "- End with one low-friction ask: a short conversation to hear more about the "
    "work. Don't mention 'internship' or 'opportunity'."
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
        f"Company: {company_name} (founder: {founder_name}, {RECIPIENT_TITLE})\n"
        f"What they're building / recent news: {recent_news}\n"
        f"Why their work is compelling: {hook}\n"
        f"Their tech stack: {', '.join(tech_stack)}\n\n"
        f"Sign the email as {settings.sender_name}, {settings.sender_role} "
        f"at {settings.sender_company}. The email is about their work, not the "
        "sender — keep any mention of the sender to the sign-off.\n\n"
        "Write the subject line and email body."
    )
