"""Constants for the discovery worker."""

# Redis key for round-robin industry cycling
DISCOVERY_RUN_COUNT_KEY = "discovery:run_count"

# JSON schema sent to Firecrawl Extract
LEAD_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "leads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "funding_stage": {"type": "string"},
                    "company_url": {"type": "string"},
                    "founder_name": {"type": "string"},
                    "founder_email": {"type": "string"},
                    "linkedin_url": {"type": "string"},
                },
                "required": ["company_name"],
            },
        }
    },
}

# Prompt template for Firecrawl Extract (use .format(limit=N))
EXTRACT_PROMPT = "Extract up to {limit} companies with their name and funding stage."
