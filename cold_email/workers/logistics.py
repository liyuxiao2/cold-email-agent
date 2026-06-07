import logging

from celery import shared_task

from cold_email.workers.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, lead_id: str) -> dict:
    """
    Triggered by FastAPI POST /leads/{id}/approve.
    Steps (to be implemented):
      1. Fetch lead + most recent draft from DB
      2. Upsert lead into Instantly (name, email, company)
      3. Add lead to campaign with draft as first email
      4. Update lead.status = 'sent'
    """
    raise NotImplementedError("Logistics worker not yet implemented")
