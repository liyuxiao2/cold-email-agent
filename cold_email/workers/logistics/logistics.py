"""Logistics worker — Celery orchestration layer.

Event-driven, per-outreach: the /api/leads/{id}/approve endpoint sets
status='approved' and dispatches logistics_task.delay(outreach_id). This task
sends the Gmail draft that drafting already created, then advances the
outreach row to 'sent'.

Stays per-outreach (unlike drafting's batch sweep) because a human approves
one outreach row at a time — the trigger is inherently a single-row event.
"""

import logging

from celery import shared_task

from cold_email.database import OUTREACH_APPROVED, OUTREACH_SENT
from cold_email.workers.logistics.constants import ERR_NO_GMAIL_DRAFT, LOGISTICS
from cold_email.workers.logistics.helpers.db_helpers import (
    fetch_outreach_status,
    fetch_send_inputs,
)
from cold_email.workers.shared.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.shared.db_helpers import update_outreach_status
from cold_email.workers.shared.errors import fail_outreach
from cold_email.workers.shared.gmail_client import send_draft

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, outreach_id: str) -> dict:
    """Send the approved outreach row's existing Gmail draft, then mark it 'sent'.

    Terminal problems return a dict (never raise) so autoretry_for doesn't retry
    a permanent state — a Gmail send failure, by contrast, propagates and is
    retried with backoff.
    """
    inputs = fetch_send_inputs(outreach_id)

    if not inputs:
        # A missing pending_sends row means "absent" whether the outreach isn't
        # approved yet (or already sent/rejected — an idempotent no-op) OR it IS
        # approved but has no draft (an anomaly worth failing). Only the
        # outreach row's own status can tell those apart.
        if fetch_outreach_status(outreach_id) != OUTREACH_APPROVED:
            logger.info(f"Outreach {outreach_id} not pending send; skipping")
            return {"status": "skipped", "reason": "outreach not pending send"}

        fail_outreach(
            outreach_id,
            ERR_NO_GMAIL_DRAFT,
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.logistics_task",
        )
        return {"status": "failed", "error": ERR_NO_GMAIL_DRAFT}

    if not inputs.gmail_draft_id:
        fail_outreach(
            outreach_id,
            ERR_NO_GMAIL_DRAFT,
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.logistics_task",
        )
        return {"status": "failed", "error": ERR_NO_GMAIL_DRAFT}

    # A transient Gmail error here raises → Celery retries this single outreach.
    send_draft(inputs.gmail_draft_id)
    update_outreach_status(outreach_id, OUTREACH_SENT)

    return {"status": "success"}
