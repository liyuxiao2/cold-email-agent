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

    apollo_api_key: str = ""
    firecrawl_api_key: str = ""
    anthropic_api_key: str = ""
    instantly_api_key: str = ""
    instantly_campaign_id: str = ""

    discovery_headcount_max: int = 150
    discovery_leads_per_run: int = 20
    discovery_person_titles: list[str] = ["founder", "co-founder", "cto", "chief technology officer"]
    discovery_person_seniorities: list[str] = ["founder", "c_suite"]
    discovery_person_locations: list[str] = ["United States", "Canada"]

    sender_name: str = "Liyu Xiao"
    sender_role: str = "Software Engineer, Ledger Team"
    sender_company: str = "Wealthsimple"


settings = Settings()
