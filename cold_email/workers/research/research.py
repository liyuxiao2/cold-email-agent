import logging
import re
from urllib.parse import urlparse

import httpx
from celery import shared_task

from cold_email.database import Lead, SyncSessionLocal
from cold_email.workers.research.constants import (
    AGGREGATOR_BLOCKLIST,
    BRAVE_SEARCH_API_URL,
    BRAVE_SEARCH_HEADERS,
)

logger = logging.getLogger(__name__)


def fetch_lead(lead_id: str) -> Lead:
    """Deduplicate and insert new leads, return list of new lead IDs."""
    with SyncSessionLocal() as session:
        lead = session.get(Lead, lead_id)
        logger.info(f"Lead fetched from DB: {lead}")
    return lead


def find_company_url(lead: Lead) -> str:
    arguments = [arg for arg in lead if arg is not None]
    params = {"q": " ".join(arguments), "count": 5, "result_filter": ["web"]}
    response = httpx.get(
        BRAVE_SEARCH_API_URL, params=params, headers=BRAVE_SEARCH_HEADERS, timeout=10
    )
    results = response.json().get("web", {}).get("results", [])
    logger.info(f"Brave Search results for finding {lead.company_name}: {results}")
    return select_best_url(results, lead)


def select_best_url(results: list[dict], lead: Lead) -> str | None:
    if not results:
        return None

    company_slug = re.sub(r"[^a-z0-9]", "", lead.company_name.lower())

    scored: list[tuple[int, str]] = []
    for result in results:
        url = result.get("url", "")
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        if any(blocked in domain for blocked in AGGREGATOR_BLOCKLIST):
            continue
        domain_slug = re.sub(r"[^a-z0-9]", "", domain)
        score = 1 if company_slug in domain_slug else 0
        scored.append((score, url))

    if not scored:
        return results[0].get("url")

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.research.research_task",
)
def research_task(self, lead_id: str) -> dict:
    """
    Dispatched by discovery_task per lead.
    Steps (to be implemented):
      1. Fetch lead from DB (use SyncSessionLocal — Celery runs sync)
      2. Call Brave Search API to find it
         POST https://api.search.brave.com/res/v1/web/search
         Header: X-Subscription-Token: settings.brave_api_key
         Take results[0]["url"] as the company homepage
      3. Scrape homepage with BeautifulSoup (requests.get → strip script/style/nav tags → .get_text())
         Fallback to FirecrawlApp.scrape_url() if content is too short (< ~300 chars)
         Truncate to ~8,000 chars before passing to LLM
      4. Call Gemini Flash for structured extraction → dict with tech_stack, recent_news, hook
         from google import genai
         client = genai.Client(api_key=settings.gemini_api_key)
         Use response_mime_type="application/json" + response_schema to enforce output shape
         (Gemini's equivalent of Claude's tool_choice="any" pattern)
      5. Insert row into research table, update lead.status = 'researched', commit
      6. Dispatch drafting_task.delay(lead_id)
    """
    lead = fetch_lead(lead_id)

    lead_url = find_company_url(lead)

    if lead_url:
        # TODO: implement scraping and LLM extraction
        pass
    else:
        logger.error(f"Could not find company URL for lead {lead_id}")
        lead.status = "failed"
        return
