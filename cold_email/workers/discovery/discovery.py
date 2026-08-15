import logging

import redis
from celery import shared_task
from firecrawl import Firecrawl

from cold_email.celery_app import app as celery_app  # noqa: F401 – ensures broker is configured
from cold_email.config import settings
from cold_email.database import RESEARCH_FOUND, Company, get_sync_session
from cold_email.workers.discovery.constants import (
    COMPANY_EXTRACT_SCHEMA,
    DISCOVERY_RUN_COUNT_KEY,
    EXTRACT_PROMPT,
    REDIS_MAX_CONNECTIONS,
)
from cold_email.workers.research import research_task

logger = logging.getLogger(__name__)

pool = redis.ConnectionPool.from_url(
    settings.celery_broker_url, max_connections=REDIS_MAX_CONNECTIONS
)


def extract_leads(urls: list[str], limit: int = 20) -> list[dict]:
    """
    Use Firecrawl Extract to pull structured company data from any listing page.
    Source-agnostic — works on startups.gallery, Crunchbase, Product Hunt, etc.
    """
    app = Firecrawl(api_key=settings.firecrawl_api_key)
    data = app.extract(
        urls=urls,
        prompt=EXTRACT_PROMPT.format(limit=limit),
        schema=COMPANY_EXTRACT_SCHEMA,
    )
    return data.data.get("leads", [])[:limit]


def get_next_url() -> str:
    """Round-robin through discovery URLs, one per run."""
    r = redis.Redis(connection_pool=pool)
    run_count = r.incr(DISCOVERY_RUN_COUNT_KEY) - 1
    index = run_count % len(settings.discovery_urls)
    url = settings.discovery_urls[index]
    logger.info(f"Discovering companies from {url}")
    return url


def save_companies_to_db(companies: list[dict]) -> list[str]:
    """Deduplicate and insert new companies, return list of new company IDs."""
    ids = []

    with get_sync_session() as session:
        batch_names = [
            company["company_name"] for company in companies if company.get("company_name")
        ]
        # Dedup protects the GLOBAL pool: a duplicate company would give two users
        # different contact pools for the same business, and the per-contact cap
        # could then be silently doubled.
        existing = (
            session.query(Company.company_name).filter(Company.company_name.in_(batch_names)).all()
        )
        existing_names = {row[0] for row in existing}
        for company in companies:
            if company.get("company_name") not in existing_names:
                existing_names.add(company["company_name"])
                new_company = Company(
                    company_name=company["company_name"],
                    funding_stage=company.get("funding_stage"),
                    company_url=company.get("company_url"),
                    founder_name=company.get("founder_name"),
                    linkedin_url=company.get("linkedin_url"),
                    research_status=RESEARCH_FOUND,
                )
                session.add(new_company)
                session.flush()
                ids.append(str(new_company.id))
        session.commit()
    return ids


def send_to_research(ids: list[str]):
    """Dispatch research_task for each new company."""
    for company_id in ids:
        research_task.delay(company_id)


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.discovery.discovery_task",
)
def discovery_task() -> dict:
    """
    Triggered by Celery Beat every Monday at 08:00.
    1. Extract companies from discovery URLs via Firecrawl Extract
    2. Deduplicate against existing companies by company_name
    3. Insert new companies with research_status='found'
    4. Dispatch research_task.delay(company_id) per new company
    """
    url = get_next_url()
    fetched_companies = extract_leads([url], limit=settings.discovery_leads_per_run)
    new_company_ids = save_companies_to_db(fetched_companies)
    send_to_research(new_company_ids)

    return {"fetched": len(fetched_companies), "saved": len(new_company_ids)}
