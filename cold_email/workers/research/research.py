"""Research worker — Celery orchestration layer.

This module contains only the @shared_task and the high-level pipeline steps.
All I/O helpers live in sibling modules:
  - extraction.py  — URL search, web scraping, LLM calls
  - db_helpers.py  — database reads/writes
"""

import logging

from celery import shared_task

from cold_email.workers.research.constants import ERR_NO_EMAIL_FOUND
from cold_email.workers.research.helpers.db_helpers import commit_research, save_founder_contact
from cold_email.workers.research.helpers.email_finder import (
    domain_from_url,
    find_email,
    should_accept_email,
)
from cold_email.workers.research.helpers.extraction import (
    call_gemini,
    parse_gemini_response,
    scrape_website,
)
from cold_email.workers.research.helpers.preflight import resolve_lead_url
from cold_email.workers.shared.constants import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    GEMINI_RATE_LIMIT,
)
from cold_email.workers.shared.db_helpers import update_lead_status
from cold_email.workers.shared.errors import handle_terminal_failure

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    # Pace per-lead Gemini calls under the free-tier 5 req/min cap so a burst
    # of requeued leads doesn't 429 itself into repeated retries.
    rate_limit=GEMINI_RATE_LIMIT,
    name="cold_email.workers.research.research_task",
)
def research_task(self, lead_id: str) -> dict:
    """
    Dispatched by discovery_task per lead.
    Steps:
      1. Fetch lead from DB
      2. Search DuckDuckGo to find the company homepage
      3. Scrape homepage with BeautifulSoup (requests.get), fallback to Firecrawl
      4. Call Gemini Flash for structured extraction
      5. Insert row into research table, update lead.status = 'researched'
         (the drafting batch sweep picks it up from there — no dispatch here)
    """
    resolution = resolve_lead_url(lead_id)

    if resolution.failure:
        return resolution.failure

    lead, lead_url = resolution.lead, resolution.url

    text = scrape_website(lead_url)
    raw = call_gemini(text, lead.company_name)
    research_dict = parse_gemini_response(raw)

    commit_research(
        lead_id=lead_id,
        tech_stack=research_dict.get("tech_stack"),
        recent_news=research_dict.get("recent_news"),
        hook=research_dict.get("hook"),
        raw_content=raw,
    )

    # Resolve the founder's email via Hunter (research has the name + domain;
    # the directory sources never carried an address). Fail fast into the DLQ
    # if we can't get a usable one, so the lead never wastes the drafting stage.
    founder_name = research_dict.get("founder_name") or lead.founder_name
    domain = domain_from_url(lead.company_url or lead_url)
    email_result = find_email(founder_name, domain)

    if not should_accept_email(email_result):
        handle_terminal_failure(
            lead_id,
            ERR_NO_EMAIL_FOUND,
            stage="research",
            task_name="cold_email.workers.research.research_task",
        )
        return {"status": "failed", "error": ERR_NO_EMAIL_FOUND}

    save_founder_contact(lead_id, founder_name, email_result["email"])
    update_lead_status(lead_id, status="researched")

    return {"status": "success"}
