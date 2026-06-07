"""Research worker — Celery orchestration layer.

This module contains only the @shared_task and the high-level pipeline steps.
All I/O helpers live in sibling modules:
  - extraction.py  — URL search, web scraping, LLM calls
  - db_helpers.py  — database reads/writes
"""

import logging

from celery import shared_task

from cold_email.workers.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.research.helpers.db_helpers import (
    commit_research,
    fetch_lead,
    update_lead_status,
)
from cold_email.workers.research.helpers.extraction import (
    call_gemini,
    find_company_url,
    parse_gemini_response,
    scrape_website,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.research.research_task",
)
def research_task(self, lead_id: str) -> dict:
    """
    Dispatched by discovery_task per lead.
    Steps:
      1. Fetch lead from DB
      2. Call Brave Search API to find the company homepage
      3. Scrape homepage with BeautifulSoup (requests.get), fallback to Firecrawl
      4. Call Gemini Flash for structured extraction
      5. Insert row into research table, update lead.status = 'researched'
      6. Dispatch drafting_task.delay(lead_id)
    """
    lead = fetch_lead(lead_id)

    if not lead:
        logger.error(f"Lead {lead_id} not found in DB")
        return {"status": "failed", "error": "Lead not found"}

    lead_url = find_company_url(lead)

    if not lead_url:
        logger.error(f"Could not find company URL for lead {lead_id}")
        update_lead_status(
            lead_id,
            status="failed",
            error_msg=f"Could not find company URL for {lead.company_name}",
        )
        return {"status": "failed", "error": "Company URL not found"}

    text = scrape_website(lead_url)
    response = call_gemini(text, lead.company_name)
    research_dict = parse_gemini_response(response)

    commit_research(
        lead_id=lead_id,
        tech_stack=research_dict.get("tech_stack"),
        recent_news=research_dict.get("recent_news"),
        hook=research_dict.get("hook"),
        raw_content=response.text,
    )

    update_lead_status(lead_id, status="researched")

    # Import here to avoid circular imports between worker modules
    from cold_email.workers.drafting import drafting_task

    drafting_task.delay(lead_id)

    return {"status": "success"}
