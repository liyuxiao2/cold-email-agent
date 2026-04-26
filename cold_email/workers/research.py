import logging

from celery import shared_task

logger = logging.getLogger(__name__)


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
    # TODO(human): implement the research worker following the steps above
    raise NotImplementedError("Research worker not yet implemented")
