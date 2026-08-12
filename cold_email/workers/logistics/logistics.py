"""Logistics worker — Celery orchestration layer.

Event-driven, per-lead: the dashboard's Approve handler sets status='approved'
and dispatches logistics_task.delay(lead_id). This task sends the Gmail draft
that drafting already created, then advances the lead to 'sent'.

Stays per-lead (unlike drafting's batch sweep) because a human approves one lead
at a time — the trigger is inherently a single-lead event.
"""

import logging

from celery import shared_task

from cold_email.workers.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.gmail_client import send_draft
from cold_email.workers.logistics.constants import ERR_NO_GMAIL_DRAFT
from cold_email.workers.logistics.helpers.db_helpers import (
    fetch_send_inputs,
    update_lead_status,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, lead_id: str) -> dict:
    """Send the approved lead's existing Gmail draft, then mark it 'sent'.

    Terminal problems return a dict (never raise) so autoretry_for doesn't retry
    a permanent state — a Gmail send failure, by contrast, propagates and is
    retried with backoff.
    """
    inputs = fetch_send_inputs(lead_id)
    if not inputs:
        # Not in pending_sends: not approved, already sent, or no draft — nothing to do.
        logger.info(f"Lead {lead_id} not pending send; skipping")
        return {"status": "skipped", "reason": "lead not pending send"}

    if not inputs.get("gmail_draft_id"):
        update_lead_status(lead_id, "failed", error_msg=ERR_NO_GMAIL_DRAFT)
        logger.warning(f"Lead {lead_id} approved but has no gmail_draft_id")
        return {"status": "failed", "error": ERR_NO_GMAIL_DRAFT}

    # A transient Gmail error here raises → Celery retries this single lead.
    send_draft(inputs["gmail_draft_id"])
    update_lead_status(lead_id, "sent")

    return {"status": "success"}
