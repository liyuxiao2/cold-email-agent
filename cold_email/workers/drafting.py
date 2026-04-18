import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
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
