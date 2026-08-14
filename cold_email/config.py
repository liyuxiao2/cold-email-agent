from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # asyncpg for FastAPI, psycopg2 derived below for Celery workers
    database_url: str = "postgresql+asyncpg://cold_email:secret@localhost:5432/cold_email"

    @computed_field
    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    firecrawl_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    hunter_api_key: str = ""

    # Gmail API — OAuth2 refresh-token flow (single sender mailbox)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_sender_email: str = ""

    # Sender identity lives in cold_email.sender_profile.PROFILE (the drafting
    # template reads it from there), not here.

    discovery_leads_per_run: int = 20

    industries: list[str] = [
        "Aerospace",
        "AI",
        "Analytics",
        "Biotech",
        "Climate",
        "Construction",
        "Consumer",
        "Cybersecurity",
        "Design",
        "DevTools",
        "Education",
        "Energy",
        "Fintech",
        "Food",
        "Gaming",
        "Hardware",
        "Health & Wellness",
        "Healthcare",
        "HR & Recruiting",
        "Infrastructure",
        "Logistics",
        "Productivity",
        "Real Estate",
        "Retail",
        "Robotics",
        "Transportation",
        "Travel",
        "Web3",
    ]

    @computed_field
    @property
    def discovery_urls(self) -> list[str]:
        """Build one startups.gallery URL per industry."""
        base = "https://startups.gallery/categories/industries"
        return [
            f"{base}/{name.lower().replace(' & ', '-').replace(' ', '-')}"
            for name in self.industries
        ]

    cors_origins: list[str] = ["*"]

    model_name: str = "gemini-flash-latest"

    model_fallback_chain: list[str] = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemini-3.5-flash-lite",
    ]


settings = Settings()
