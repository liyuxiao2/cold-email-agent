"""Research worker — Celery orchestration layer.

This module contains only the @shared_task and the high-level pipeline steps.
All I/O helpers live in sibling modules:
  - extraction.py      — URL search, web scraping, LLM calls
  - contact_finder.py  — Hunter Domain Search + contact classification
  - db_helpers.py      — database reads/writes
"""

import logging

from celery import shared_task

from cold_email.database import RESEARCH_RESEARCHED
from cold_email.workers.research.constants import ERR_NO_ELIGIBLE_CONTACTS, RESEARCH
from cold_email.workers.research.helpers.contact_finder import (
    classify_contacts,
    domain_from_url,
    find_contacts,
    has_eligible_contact,
)
from cold_email.workers.research.helpers.db_helpers import commit_research, save_contacts
from cold_email.workers.research.helpers.extraction import (
    call_llm_extraction,
    parse_llm_response,
    scrape_website,
)
from cold_email.workers.research.helpers.preflight import resolve_company_url
from cold_email.workers.shared.constants import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    LLM_RATE_LIMIT,
)
from cold_email.workers.shared.db_helpers import update_company_research_status
from cold_email.workers.shared.errors import fail_company

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    # Pace per-company LLM calls under the free-tier 5 req/min cap so a burst
    # of requeued companies doesn't 429 itself into repeated retries.
    rate_limit=LLM_RATE_LIMIT,
    name="cold_email.workers.research.research_task",
)
def research_task(self, company_id: str) -> dict:
    """
    Dispatched by discovery_task per company.
    Steps:
      1. Resolve the official company homepage (DuckDuckGo + scoring)
      2. Scrape it (BeautifulSoup, Firecrawl fallback)
      3. LLM structured extraction
      4. Insert a research row
      5. Fetch the contact pool from Hunter Domain Search and classify it
      6. Save EVERY contact, then gate on whether any is eligible
    """
    resolution = resolve_company_url(company_id)
    if resolution.failure:
        return resolution.failure

    company, company_url = resolution.company, resolution.url

    text = scrape_website(company_url)
    raw = call_llm_extraction(text, company.company_name)
    research_dict = parse_llm_response(raw)

    commit_research(
        company_id=company_id,
        tech_stack=research_dict.get("tech_stack"),
        recent_news=research_dict.get("recent_news"),
        hook=research_dict.get("hook"),
        raw_content=raw,
    )

    founder_name = research_dict.get("founder_name") or company.founder_name
    contacts = classify_contacts(find_contacts(domain_from_url(company_url)), founder_name)

    # Save BEFORE gating: a company that fails research keeps its contact rows,
    # so loosening DECISION_MAKER_PATTERNS later can re-classify them instead of
    # re-spending Hunter credits.
    save_contacts(company_id, contacts)

    if not has_eligible_contact(contacts):
        fail_company(
            company_id,
            ERR_NO_ELIGIBLE_CONTACTS,
            stage=RESEARCH,
            task_name="cold_email.workers.research.research_task",
        )
        return {"status": "failed", "error": ERR_NO_ELIGIBLE_CONTACTS}

    update_company_research_status(company_id, RESEARCH_RESEARCHED)
    return {"status": "success", "contacts": len(contacts)}
