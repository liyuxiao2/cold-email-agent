from cold_email.workers.constants import DEFAULT_RETRY_DELAY
from cold_email.workers.constants import DEFAULT_MAX_RETRIES
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
    Steps:
      1. fetch_draft_inputs(lead_id) — lead + latest research
      2. generate_email_draft(inputs) — Gemini → {subject, body}
      3. create_draft(...) — Gmail API, returns the draft id
      4. commit_draft(...) — persist row + gmail_draft_id
      5. update_lead_status(lead_id, "drafted") — stop (HITL pause)
    """
    # TODO(human): orchestrate the steps above.
    #   - Guard the terminal failures by RETURNING (never raise — autoretry
    #     would pointlessly retry): missing inputs, missing founder_email,
    #     or an empty draft from generate_email_draft.
    #   - On success, dispatch NOTHING further — the pipeline pauses here for
    #     human review. Return {"status": "success"}.
    raise NotImplementedError
