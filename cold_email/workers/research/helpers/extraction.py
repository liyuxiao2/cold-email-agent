"""Extraction helpers for the research worker.

Handles all I/O-heavy work: searching for company URLs, scraping web pages,
and calling the Gemini LLM for structured data extraction.
"""

import json
import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from firecrawl import FirecrawlApp

from cold_email.config import settings
from cold_email.database import Lead
from cold_email.prompts.research import (
    EXTRACTION_SYSTEM,
    ResearchExtraction,
    build_extraction_messages,
)
from cold_email.workers.research.constants import (
    AGGREGATOR_BLOCKLIST,
    DOMAIN_MATCH_SCORE,
    DOMAIN_MISMATCH_SCORE,
    HTTP_STATUS_OK,
    JSON_BLOCK_END_MARKER,
    JSON_BLOCK_START_MARKER,
    MAX_SCRAPED_TEXT_LEN,
    MIN_SCRAPED_TEXT_LEN,
    SCRAPE_EXCLUDE_TAGS,
    SCRAPE_TIMEOUT,
    SEARCH_RESULT_COUNT,
    SLUG_CLEANUP_REGEX,
)
from cold_email.workers.shared.llm import generate_json

logger = logging.getLogger(__name__)


def find_company_url(lead: Lead) -> str | None:
    """Use DuckDuckGo (keyless) to find the best URL for a company.

    ddgs raises on transient failures (rate limiting, network) rather than
    returning a status code, so those propagate up and the research task's
    autoretry recovers the lead. An empty result list is a genuine
    "not found" and select_best_url returns None (terminal).
    """
    query_parts = [lead.company_name, lead.funding_stage]
    query = " ".join(arg for arg in query_parts if arg)
    results = DDGS().text(query, max_results=SEARCH_RESULT_COUNT)
    logger.info(f"DuckDuckGo results for finding {lead.company_name}: {results}")
    # ddgs returns the URL under 'href'; select_best_url expects 'url'.
    candidates = [{"url": r["href"]} for r in results if r.get("href")]
    return select_best_url(candidates, lead)


def select_best_url(results: list[dict], lead: Lead) -> str | None:
    """Score search results and return the URL most likely to be the company homepage."""
    if not results:
        return None

    company_slug = re.sub(SLUG_CLEANUP_REGEX, "", lead.company_name.lower())

    scored: list[tuple[int, str]] = []
    for result in results:
        url = result.get("url", "")
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        if any(blocked in domain for blocked in AGGREGATOR_BLOCKLIST):
            continue
        domain_slug = re.sub(SLUG_CLEANUP_REGEX, "", domain)
        score = DOMAIN_MATCH_SCORE if company_slug in domain_slug else DOMAIN_MISMATCH_SCORE
        scored.append((score, url))

    if not scored:
        return results[0].get("url")

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def scrape_website(lead_url: str) -> str:
    """Scrape a URL with requests/BeautifulSoup, falling back to Firecrawl."""
    try:
        response = requests.get(lead_url, timeout=SCRAPE_TIMEOUT)
        if response.status_code == HTTP_STATUS_OK:
            soup = BeautifulSoup(response.content, "html.parser")

            for tag in soup(SCRAPE_EXCLUDE_TAGS):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)

            # Fall back to Firecrawl if the scraped text is too short
            if len(text) < MIN_SCRAPED_TEXT_LEN:
                firecrawl_app = FirecrawlApp(api_key=settings.firecrawl_api_key)
                firecrawl_response = firecrawl_app.scrape(lead_url)
                text = firecrawl_response.markdown or ""

            # Truncate to avoid exceeding LLM context limits
            if len(text) > MAX_SCRAPED_TEXT_LEN:
                text = text[:MAX_SCRAPED_TEXT_LEN]

            return text
    except Exception as e:
        logger.error(f"Error scraping {lead_url}: {e}")
    return ""


def call_gemini(text: str, company_name: str) -> str:
    """Extract structured research fields from scraped content via the LLM.

    Routes through generate_json (provider-agnostic): the configured chain
    handles model/provider fallback. Returns the model's raw JSON text.
    """
    return generate_json(
        system=EXTRACTION_SYSTEM,
        prompt=build_extraction_messages(company_name=company_name, scraped_content=text),
        schema=ResearchExtraction,
    )


def parse_gemini_response(raw: str) -> dict:
    """Parse the structured JSON payload from a raw LLM response string.

    Returns an empty dict if the text is missing or malformed.
    """
    if not raw:
        return {}

    raw_json = raw.strip()

    # Strip optional markdown code fence (```json ... ```)
    if raw_json.startswith(JSON_BLOCK_START_MARKER) and raw_json.endswith(
        JSON_BLOCK_END_MARKER
    ):
        raw_json = raw_json[len(JSON_BLOCK_START_MARKER) : -len(JSON_BLOCK_END_MARKER)].strip()

    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON response: {raw_json}")
        return {}
