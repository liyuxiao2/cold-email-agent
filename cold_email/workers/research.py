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
      1. Fetch lead from DB
      2. Scrape company_url with Firecrawl (+ /blog, /engineering up to 3 pages)
      3. Truncate concatenated markdown to ~8,000 tokens
      4. Call Claude with extraction prompt (tool use → structured JSON)
      5. Insert row into research table
      6. Update lead.status = 'researched'
      7. Dispatch drafting_task.delay(lead_id)
    """
    raise NotImplementedError("Research worker not yet implemented")
