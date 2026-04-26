import logging

import redis
from celery import shared_task
from firecrawl import Firecrawl

from cold_email.celery_app import app as celery_app  # noqa: F401 – ensures broker is configured
from cold_email.config import settings
from cold_email.database import Lead, SyncSessionLocal
from cold_email.workers.discovery.constants import (
    DISCOVERY_RUN_COUNT_KEY,
    EXTRACT_PROMPT,
    LEAD_EXTRACT_SCHEMA,
)
from cold_email.workers.research import research_task

logger = logging.getLogger(__name__)


def extract_leads(urls: list[str], limit: int = 20) -> list[dict]:
    """
    Use Firecrawl Extract to pull structured lead data from any listing page.
    Source-agnostic — works on startups.gallery, Crunchbase, Product Hunt, etc.
    """
    app = Firecrawl(api_key=settings.firecrawl_api_key)
    data = app.extract(
        urls=urls,
        prompt=EXTRACT_PROMPT.format(limit=limit),
        schema=LEAD_EXTRACT_SCHEMA,
    )
    return data.data.get("leads", [])[:limit]


def get_next_url() -> str:
    """Round-robin through discovery URLs, one per run."""
    r = redis.from_url(settings.celery_broker_url)
    run_count = r.incr(DISCOVERY_RUN_COUNT_KEY) - 1
    index = run_count % len(settings.discovery_urls)
    url = settings.discovery_urls[index]
    logger.info(f"Discovering leads from {url}")
    return url


def save_leads_to_db(leads: list[dict]) -> list[str]:
    """Deduplicate and insert new leads, return list of new lead IDs."""
    ids = []

    with SyncSessionLocal() as session:
        batch_names = [lead["company_name"] for lead in leads if lead.get("company_name")]
        existing = session.query(Lead.company_name).filter(Lead.company_name.in_(batch_names)).all()
        existing_names = {row[0] for row in existing}
        for lead in leads:
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
                ids.append(str(new_lead.id))
        session.commit()
    return ids


def send_to_research(ids: list[str]):
    """Dispatch research_task for each new lead."""
    for lead_id in ids:
        research_task.delay(lead_id)


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
    url = get_next_url()
    fetched_leads = extract_leads([url], limit=settings.discovery_leads_per_run)
    new_lead_ids = save_leads_to_db(fetched_leads)
    send_to_research(new_lead_ids)

    return {"fetched": len(fetched_leads)}
