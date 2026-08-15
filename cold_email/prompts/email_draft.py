"""LLM contract for candidate-outreach drafting.

The model does NOT write the whole email — the template (email_template.py) owns
structure and tone. The model returns only the *contextual* pieces via the
EmailDraftContext schema: the subject, the two company-specific interest phrases,
a tailored introduction sentence, and a selection of experience bullets extracted
from the resume. Everything else is filled deterministically.
"""

from pydantic import BaseModel, Field

RECIPIENT_TITLE = "Founder"  # startups.gallery leads are founders; no title column upstream.

WHY_COMPANY_EXAMPLES = (
    "Good Examples:\n"
    "  * 'I\\'ve been following your development of a universal API that simplifies access to critical GPS, safety, "
    "and dash cam data from 100+ telematics integrations for commercial trucking. I\\'m really drawn to the high-ownership "
    "culture you\\'ve built, and wanted to see if there might be a fit for me to contribute to the team.'\n"
    "  * 'I\\'ve been using Apollo for a while now and love what you guys are doing for cold outreach. Personally, "
    "Apollo was the reason I found my first internship (it\\'s how I sourced 80% of my hiring manager contacts), "
    "and I\\'d love the chance to contribute to your mission and learn from the team.'\n"
    "  * 'Congrats on the recent seed round! Seeing how Nourish leverages AI-native metabolic care to drive "
    "measurable outcomes in chronic disease management is incredibly exciting, and I wanted to see if there might "
    "be a fit for me to contribute to the engineering team.'"
)

SUBJECT_EXAMPLES = (
    "Good Examples:\n"
    "  * 'Engineering / Nourish'\n"
    "  * 'Wealthsimple / Nourish'\n"
    "  * 'McMaster CS / Nourish'"
)

INTRO_EXAMPLES = (
    "Good Examples:\n"
    "  * 'My name is Liyu, a Computer Science student at McMaster currently interning at Wealthsimple and previously at IBM.'\n"
    "  * 'I’m Liyu, a second-year computer science student based in Toronto, currently interning at IBM as a software engineer.'\n"
    "  * 'My name is Liyu; I\\'m a CS student at McMaster with software engineering experience at IBM and Wealthsimple.'"
)

TAILORED_BULLETS_EXAMPLES = (
    "Good Examples:\n"
    "  * 'Wealthsimple: Architected a Ruby adapter and led development of an RESP engine for 3M+ clients, relevant to Nourish\\'s AI-native platform'\n"
    "  * 'IBM: Built backend infrastructure for 25,000+ authors, cutting database cluster calls by 66%'\n"
    "  * 'Wealthsimple: Supported the core financial ledger system processing transactions for over 3 million Canadians'"
)

EMAIL_DRAFT_SYSTEM = (
    "You fill the contextual slots of a fixed candidate-outreach email a software "
    "engineer sends to a startup founder. You do NOT write the whole email — only "
    "the fields requested.\n\n"
    "Rules:\n"
    "- `why_company`: write a brief, natural 2-3 sentence paragraph explaining why you are reaching out to "
    "this specific company. Avoid repetitive or formulaic phrasing (do NOT use 'I'm particularly interested in' "
    "and 'I'm drawn to' together, as it sounds robotic). Instead, make it flow naturally. You can congratulate them "
    "on recent news/funding, reference a specific project or tech stack, or explain why their mission/problem is "
    "compelling to you, and close the paragraph with a transition to seeing if there might be a fit to contribute "
    "to the team.\n"
    f"  {WHY_COMPANY_EXAMPLES}\n"
    "- `intro`: write a tailored first-person introduction sentence that highlights relevant "
    "aspects of your background (from the resume) that align with the company's domain, tech stack, or challenges. "
    "Keep it to exactly one sentence, professional, and natural-sounding.\n"
    f"  {INTRO_EXAMPLES}\n"
    "- `tailored_bullets`: choose or generate the 3 experience bullets from the provided resume that "
    "are MOST relevant to this company, ordered most-relevant first. Return each as a "
    "'Label: achievement' string (where Label is the company/project name, e.g., 'Wealthsimple: Developed a financial hold...'). "
    "You may lightly rephrase/tailor them for relevance, but never fabricate achievements or change the numbers/facts.\n"
    f"  {TAILORED_BULLETS_EXAMPLES}\n"
    "- `subject`: a short, specific subject line referencing the company by name. No "
    "clickbait, no 'opportunity'.\n"
    f"  {SUBJECT_EXAMPLES}\n"
    "- Specific over generic throughout."
)


class EmailDraftContext(BaseModel):
    """Contextual slots the LLM fills; the template supplies everything else."""

    subject: str = Field(description="Short, company-specific subject line")
    why_company: str = Field(
        description="A 2-3 sentence paragraph detailing why you are interested in the company (referencing their tech, projects, news, or mission) and wanting to explore a fit to contribute."
    )
    intro: str = Field(
        description="A tailored, professional first-person introduction sentence based on the resume and company context"
    )
    tailored_bullets: list[str] = Field(
        description="Exactly 3 'Label: achievement' strings selected and tailored from the resume, ordered most-relevant first, "
    )


def build_email_draft_messages(
    founder_name: str,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
    resume_text: str,
) -> str:
    """Build the drafting prompt: company research + the sender's full resume."""
    return (
        f"Company: {company_name} (recipient: {founder_name}, {RECIPIENT_TITLE})\n"
        f"What they're building / recent news: {recent_news}\n"
        f"Why their work is compelling: {hook}\n"
        f"Their tech stack: {', '.join(tech_stack)}\n\n"
        f"Sender's Resume:\n{resume_text}\n\n"
        "Fill the requested fields."
    )
