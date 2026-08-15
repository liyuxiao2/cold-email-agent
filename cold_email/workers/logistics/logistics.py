"""Logistics worker — Celery orchestration layer.

Three tasks:

  * send_due_task (Beat, every 5 minutes) — claims every 'approved' outreach
    row that is now due (pending_sends already encodes "due":
    scheduled_send_at IS NULL OR <= now()) by flipping it to 'sending' IN THE
    SAME UPDATE that selects it (claim_due_sends), then dispatches
    logistics_task for exactly the ids that UPDATE actually returned. Celery
    guarantees at-least-once TASK delivery, so a scanner over rows that only
    leave the set on success would eventually dispatch the same row twice --
    a cold email delivered twice to a founder cannot be undone.
  * logistics_task — sends ONE claimed draft. Re-checks the row is still
    'sending' before calling send_draft (the second guard: a duplicate
    delivery of the SAME Celery task must not send again either), then
    advances it to 'sent'.
  * reap_stuck_sends (Beat, hourly) — a row can be claimed 'sending' and then
    orphaned by a hard worker crash before it reaches 'sent' or 'failed'. Its
    outcome (sent or not) is unknown, so it is dead-lettered for a human to
    verify the mailbox, never auto-retried -- auto-retrying a send whose
    outcome is unknown is exactly how a duplicate send happens.
"""

import logging

from celery import shared_task
from googleapiclient.errors import HttpError

from cold_email.auth.gmail_creds import resolve_gmail_credentials
from cold_email.database import OUTREACH_SENDING, OUTREACH_SENT
from cold_email.workers.logistics.constants import (
    ERR_GMAIL_DISCONNECTED,
    ERR_NO_GMAIL_DRAFT,
    ERR_SEND_FAILED,
    ERR_SEND_STATUS_UNKNOWN,
    LOGISTICS,
)
from cold_email.workers.logistics.helpers.db_helpers import (
    claim_due_sends,
    fetch_outreach_status_and_owner,
    fetch_owning_user,
    fetch_send_row,
    find_stuck_sending,
)
from cold_email.workers.shared.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.shared.db_helpers import update_outreach_status
from cold_email.workers.shared.errors import fail_outreach
from cold_email.workers.shared.gmail_client import send_draft

logger = logging.getLogger(__name__)

# How long a claimed 'sending' row may sit unresolved before reap_stuck_sends
# dead-letters it. Long enough that an in-flight send (a real Gmail round
# trip) is never mistaken for an abandoned claim.
STUCK_SENDING_MINUTES = 30


@shared_task(name="cold_email.workers.logistics.send_due_task")
def send_due_task() -> dict:
    """Claim and dispatch every approved outreach row that is now due.

    Runs every 5 minutes (see celery_app.py). pending_sends already carries
    the `scheduled_send_at IS NULL OR <= now()` clause (migration 006), so an
    approval with no schedule goes out on the next tick.
    """
    claimed = claim_due_sends()

    for outreach_id in claimed:
        logistics_task.delay(outreach_id)

    if claimed:
        logger.info(f"Claimed and dispatched {len(claimed)} due send(s)")
    return {"status": "success", "dispatched": len(claimed)}


@shared_task(name="cold_email.workers.logistics.reap_stuck_sends")
def reap_stuck_sends() -> dict:
    """Dead-letter rows stuck at 'sending' -- a worker died mid-send.

    Surfaced, NOT auto-retried. The row was claimed and may or may not have
    been delivered; retrying a send whose outcome is unknown is precisely how
    a double-send happens. A human verifies the mailbox, then retries via the
    DLQ.
    """
    stuck = find_stuck_sending(STUCK_SENDING_MINUTES)

    for outreach_id in stuck:
        fail_outreach(
            outreach_id,
            ERR_SEND_STATUS_UNKNOWN,
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.reap_stuck_sends",
        )

    if stuck:
        logger.warning(f"Reaped {len(stuck)} stuck 'sending' row(s) to the DLQ")
    return {"status": "success", "reaped": len(stuck)}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, outreach_id: str) -> dict:
    """Send one claimed draft, then mark it 'sent'.

    Terminal problems return a dict (never raise) so autoretry_for doesn't
    retry a permanent state; an unexpected error (DB blip, a non-HttpError
    Gmail failure) propagates and is retried with backoff.
    """
    status_and_owner = fetch_outreach_status_and_owner(outreach_id)
    if status_and_owner is None:
        return {"status": "skipped", "reason": "not_found"}
    status, user_id = status_and_owner

    # The second guard. send_due_task's claim UPDATE is the first; this
    # re-check is the second, catching a duplicate Celery delivery of the
    # SAME dispatch (Celery's at-least-once task delivery) rather than a
    # second scan. Without it, redelivering this task after it already
    # advanced the row to 'sent' would send the draft again.
    if status != OUTREACH_SENDING:
        logger.info(f"Outreach {outreach_id} is {status}, not sending; skipping")
        return {"status": "skipped", "reason": "not_claimed"}

    row = fetch_send_row(outreach_id)
    if row is None or not row.gmail_draft_id:
        fail_outreach(
            outreach_id,
            ERR_NO_GMAIL_DRAFT,
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.logistics_task",
        )
        return {"status": "failed", "error": ERR_NO_GMAIL_DRAFT}

    # Always the OWNING user's mailbox -- never a global sender or whoever
    # happens to be calling.
    user = fetch_owning_user(user_id)
    creds = resolve_gmail_credentials(user) if user else None

    if creds is None:
        fail_outreach(
            outreach_id,
            ERR_GMAIL_DISCONNECTED,
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.logistics_task",
        )
        return {"status": "failed", "error": ERR_GMAIL_DISCONNECTED}

    try:
        message_id = send_draft(creds, row.gmail_draft_id)
    except HttpError as exc:
        # The user can delete the draft by hand in Gmail between approving and
        # the scheduled send. Terminal for this row only, never for the scan.
        fail_outreach(
            outreach_id,
            f"{ERR_SEND_FAILED}: {exc}",
            stage=LOGISTICS,
            task_name="cold_email.workers.logistics.logistics_task",
        )
        return {"status": "failed", "error": str(exc)}

    update_outreach_status(outreach_id, OUTREACH_SENT)
    return {"status": "success", "message_id": message_id}
