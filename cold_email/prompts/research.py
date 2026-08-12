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
            "One specific, concrete angle for a cold email from a fintech engineer "
            "with ledger/payment infrastructure experience — what problem might they "
            "be facing that this person could help with?"
        )
    )


def build_extraction_messages(company_name: str, scraped_content: str) -> str:
    return (
        f"Company: {company_name}\n"
        f"Scraped content:\n---\n{scraped_content}\n---\n"
        "Extract the research fields."
    )
