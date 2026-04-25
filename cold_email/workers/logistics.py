import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
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
