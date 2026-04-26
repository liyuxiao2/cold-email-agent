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
    anthropic_api_key: str = ""
    instantly_api_key: str = ""
    instantly_campaign_id: str = ""
    brave_api_key: str = ""
    gemini_api_key: str = ""

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

    sender_name: str = "Liyu Xiao"
    sender_role: str = "Software Engineer, Ledger Team"
    sender_company: str = "Wealthsimple"


settings = Settings()
