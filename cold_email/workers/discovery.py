import logging

from celery import shared_task
from firecrawl import Firecrawl

from cold_email.celery_app import app as celery_app  # noqa: F401 – ensures broker is configured
from cold_email.config import settings
from cold_email.database import Lead, SyncSessionLocal
from cold_email.workers.research import research_task

logger = logging.getLogger(__name__)


def extract_leads(urls: list[str], limit: int = 20) -> list[dict]:
    """
    Use Firecrawl Extract to pull structured lead data from any listing page.
    Source-agnostic — works on startups.gallery, Crunchbase, Product Hunt, etc.
    """
    schema = {
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

    app = Firecrawl(api_key=settings.firecrawl_api_key)
    data = app.extract(
        urls=urls,
        prompt=f"Extract up to {limit} companies with their name and funding stage.",
        schema=schema,
    )
    return data.data.get("leads", [])[:limit]


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.discovery.discovery_task",
)
def discovery_task() -> dict:
    """
    Triggered by Celery Beat every Monday at 08:00.
    1. Extract leads from discovery URLs via Firecrawl Extract
    2. Deduplicate against existing leads by company_name
    3. Insert new leads with status='found'
    4. Dispatch research_task.delay(lead_id) per new lead
    """
    fetched_leads = extract_leads(settings.discovery_urls, limit=settings.discovery_leads_per_run)
    new_lead_ids = []

    with SyncSessionLocal() as session:
        existing = session.query(Lead.company_name).all()
        existing_names = {row[0] for row in existing}
        for lead in fetched_leads:
            if lead.get("company_name") not in existing_names:
                existing_names.add(lead["company_name"])
                new_lead = Lead(
                    company_name=lead["company_name"],
                    funding_stage=lead.get("funding_stage"),
                    company_url=lead.get("company_url"),
                    founder_name=lead.get("founder_name"),
                    founder_email=lead.get("founder_email"),
                    linkedin_url=lead.get("linkedin_url"),
                )
                session.add(new_lead)
                session.flush()
                new_lead_ids.append(str(new_lead.id))
        session.commit()

    for lead_id in new_lead_ids:
        research_task.delay(lead_id)

    return {"fetched": len(fetched_leads)}
