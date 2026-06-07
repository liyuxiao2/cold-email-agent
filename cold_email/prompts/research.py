EXTRACTION_SYSTEM = (
    "You are a research assistant for a software engineer at a fintech company. "
    "Given scraped content from a company's website, extract structured information "
    "for a targeted cold email. Return a JSON object only, no prose."
)

EXTRACTION_TOOL = {
    "name": "extract_research",
    "description": "Extract structured research from company website content",
    "input_schema": {
        "type": "object",
        "properties": {
            "tech_stack": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Technologies mentioned or strongly implied "
                    "(languages, databases, infrastructure)"
                ),
            },
            "recent_news": {
                "type": "string",
                "description": (
                    "One sentence describing the most recent notable thing "
                    "(funding, product launch, engineering blog topic)"
                ),
            },
            "hook": {
                "type": "string",
                "description": (
                    "One specific, concrete angle for a cold email from a fintech engineer "
                    "with ledger/payment infrastructure experience — what problem might they "
                    "be facing that this person could help with?"
                ),
            },
        },
        "required": ["tech_stack", "recent_news", "hook"],
    },
}


def build_extraction_messages(company_name: str, scraped_content: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Company: {company_name}\n"
                f"Scraped content:\n---\n{scraped_content}\n---\n"
                "Extract the research fields."
            ),
        }
    ]
