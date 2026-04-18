import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.discovery.discovery_task",
)
def discovery_task(self) -> dict:
    """
    Triggered by Celery Beat every Monday at 08:00.
    Steps (to be implemented):
      1. Query Apollo.io for early-stage fintech companies
      2. Find technical founder/CTO contact per company
      3. Deduplicate against existing leads by founder_email / company_url
      4. Insert new leads with status='found'
      5. Dispatch research_task.delay(lead_id) per new lead
    """
    raise NotImplementedError("Discovery worker not yet implemented")
