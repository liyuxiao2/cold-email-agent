import logging

from celery import shared_task

from cold_email.workers.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.drafting.drafting_task",
)
def drafting_task(self, lead_id: str) -> dict:
    """
    Dispatched by research_task per lead.
    Steps (to be implemented):
      1. Fetch lead + research from DB
      2. Call Claude with email draft prompt (tool use → {subject, body})
      3. Insert row into drafts table
      4. Update lead.status = 'drafted'
      5. Stop — HITL pause, no further chaining
    """
    raise NotImplementedError("Drafting worker not yet implemented")
