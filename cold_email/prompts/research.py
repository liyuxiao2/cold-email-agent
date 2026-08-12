from pydantic import BaseModel, Field

EXTRACTION_SYSTEM = (
    "You are a research assistant for a software engineer at a fintech company. "
    "Given scraped content from a company's website, extract structured information "
    "for a targeted cold email. Return a JSON object only, no prose."
)


class ResearchExtraction(BaseModel):
    """Structured research fields — used as the Gemini response_schema.

    Field descriptions are sent to the model as part of the JSON schema, so
    they double as extraction instructions.
    """

    founder_name: str = Field(
        description=(
            "Full name of the founder or CEO if identifiable from the content "
            "(e.g. an about/team page). Empty string if not found — do not guess."
        )
    )
    tech_stack: list[str] = Field(
        description=(
            "Technologies mentioned or strongly implied "
            "(languages, databases, infrastructure)"
        )
    )
    recent_news: str = Field(
        description=(
            "One sentence describing the most recent notable thing "
            "(funding, product launch, engineering blog topic)"
        )
    )
    hook: str = Field(
        description=(
            "The single most compelling, specific thing about THIS company's work "
            "that would make an engineer genuinely want to work here — anchored to a "
            "concrete detail (a recent raise, a product launch, a hard technical "
            "problem they're tackling, or their mission). Phrase it as authentic "
            "interest in what they're building. Do NOT frame it as a problem the "
            "sender could solve or a skill the sender could offer — it's about why "
            "their work is exciting, not about the sender."
        )
    )


def build_extraction_messages(company_name: str, scraped_content: str) -> str:
    return (
        f"Company: {company_name}\n"
        f"Scraped content:\n---\n{scraped_content}\n---\n"
        "Extract the research fields."
    )
