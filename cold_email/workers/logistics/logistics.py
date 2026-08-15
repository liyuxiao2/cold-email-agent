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
  * logistics_task — sends ONE claimed draft, then advances it to 'sent'.
    Guards against a duplicate delivery of the SAME dispatch (Celery's
    at-least-once task delivery) by atomically consuming the draft's
    gmail_draft_id as a one-time ticket (claim_send_ticket) before ever
    calling send_draft -- see the function's own docstring for why a bare
    status re-check cannot do this alone. Everything from the moment that
    ticket is claimed onward is handled inline and never re-raised, so a
    post-send failure can never cause Celery's autoretry to call send_draft
    a second time.
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
    ERR_SEND_OUTCOME_UNKNOWN,
    ERR_SEND_STATUS_UNKNOWN,
    LOGISTICS,
)
from cold_email.workers.logistics.helpers.db_helpers import (
    claim_due_sends,
    claim_send_ticket,
    fetch_outreach_status_and_owner,
    fetch_owning_user,
    fetch_send_row,
    find_stuck_sending,
)
from cold_email.workers.shared.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.shared.db_helpers import record_dead_letter, update_outreach_status
from cold_email.workers.shared.errors import fail_outreach
from cold_email.workers.shared.gmail_client import send_draft

logger = logging.getLogger(__name__)

# How long a claimed 'sending' row may sit unresolved before reap_stuck_sends
# dead-letters it. Long enough that an in-flight send (a real Gmail round
# trip) is never mistaken for an abandoned claim.
STUCK_SENDING_MINUTES = 30

_TASK_NAME = "cold_email.workers.logistics.logistics_task"


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


def _dead_letter_unknown_outcome(outreach_id: str, detail: str) -> None:
    """Record an ambiguous send outcome WITHOUT touching status.

    Deliberately not fail_outreach: that sets status='failed', which reads as
    "definitely never sent" -- exactly the false claim this path exists to
    avoid making. Status is left wherever it was ('sending'); if even this
    write fails (the DB is genuinely unreachable), the row is still
    recoverable -- it sits at 'sending' until reap_stuck_sends's own
    dead-letter pass catches it on its next hourly run.
    """
    try:
        record_dead_letter(
            outreach_id=outreach_id,
            task_name=_TASK_NAME,
            stage=LOGISTICS,
            error_msg=f"{ERR_SEND_OUTCOME_UNKNOWN} ({detail})",
        )
    except Exception:
        logger.exception(
            f"Outreach {outreach_id}: could not record the dead letter for an "
            "unknown send outcome either; relying on reap_stuck_sends to catch it"
        )


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, outreach_id: str) -> dict:
    """Send one claimed draft, then mark it 'sent'.

    Two claims, not one:
      1. send_due_task's claim_due_sends -- approved -> sending -- guards
         against two overlapping SCANS both dispatching the same row.
      2. claim_send_ticket below -- guards against two concurrent
         EXECUTIONS of this same dispatched task (Celery's at-least-once
         delivery can redeliver one message to two workers) both reaching
         send_draft. A bare status re-check is read-then-act, not atomic:
         both concurrent executions would read 'sending' before either
         advances it, so the check alone cannot serialize them. Nulling
         drafts.gmail_draft_id -- the one column naming the resource about
         to be spent -- is a compare-and-swap in the same idiom as the
         scanner's own claim: whichever caller's UPDATE still finds the
         expected (non-null) value wins; the other's WHERE clause matches
         nothing once the winner has already claimed it.

    Ordering, deliberately: mark 'sent' AFTER calling send_draft, not before.
    Marking first would make a lost email look delivered if the worker died
    between the write and the actual Gmail call -- an invisible failure with
    no signal anywhere. Marking after leaves a narrower, unavoidable window
    (Gmail accepts the message, then the status write itself fails) but that
    window is visible and safe: nothing here re-raises past the point the
    ticket is claimed, so Celery's autoretry can never trigger a second
    send_draft call for this row, and a failure in that window is
    dead-lettered as UNKNOWN (never 'failed' -- that would falsely claim the
    email was never sent) for a human to check the mailbox and retry via the
    DLQ once they know which way it actually went. autoretry_for stays
    `Exception` at the decorator level so genuinely pre-send failures (the
    status/row/credential lookups above, or claim_send_ticket's own DB call)
    still retry with backoff -- nothing has been claimed or sent yet at that
    point, so a retry there is exactly as safe as it always was.
    """
    status_and_owner = fetch_outreach_status_and_owner(outreach_id)
    if status_and_owner is None:
        return {"status": "skipped", "reason": "not_found"}
    status, user_id = status_and_owner

    if status != OUTREACH_SENDING:
        logger.info(f"Outreach {outreach_id} is {status}, not sending; skipping")
        return {"status": "skipped", "reason": "not_claimed"}

    row = fetch_send_row(outreach_id)
    if row is None:
        fail_outreach(outreach_id, ERR_NO_GMAIL_DRAFT, stage=LOGISTICS, task_name=_TASK_NAME)
        return {"status": "failed", "error": ERR_NO_GMAIL_DRAFT}

    if not row.gmail_draft_id:
        # A drafts row exists but has no gmail_draft_id left to send. Under
        # healthy drafting this never happens on a fresh row (create_draft
        # always populates it); the only way to reach this state is that
        # claim_send_ticket already consumed it -- a prior or concurrent
        # execution of this same 'sending' row owns whatever happens next.
        logger.info(f"Outreach {outreach_id} send ticket already claimed; skipping")
        return {"status": "skipped", "reason": "already_claimed"}

    # Always the OWNING user's mailbox -- never a global sender or whoever
    # happens to be calling.
    user = fetch_owning_user(user_id)
    creds = resolve_gmail_credentials(user) if user else None

    if creds is None:
        fail_outreach(outreach_id, ERR_GMAIL_DISCONNECTED, stage=LOGISTICS, task_name=_TASK_NAME)
        return {"status": "failed", "error": ERR_GMAIL_DISCONNECTED}

    draft_id = row.gmail_draft_id
    if not claim_send_ticket(row.draft_id, draft_id):
        logger.info(f"Outreach {outreach_id} send ticket already claimed; skipping")
        return {"status": "skipped", "reason": "already_claimed"}

    # Past this line the ticket is spent -- there is no safe way back to "not
    # sent". Nothing below re-raises.
    try:
        message_id = send_draft(creds, draft_id)
    except HttpError as exc:
        # A definitive, terminal answer from Gmail itself (e.g. 404 -- the
        # user deleted the draft by hand between approving and the scheduled
        # send). Not ambiguous: Gmail is telling us outright nothing was
        # sent, so it is safe to fail this row only, never the whole scan.
        fail_outreach(
            outreach_id, f"{ERR_SEND_FAILED}: {exc}", stage=LOGISTICS, task_name=_TASK_NAME
        )
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        # Anything else (timeout, connection reset) means we cannot tell
        # whether Gmail received the request before the error struck.
        _dead_letter_unknown_outcome(outreach_id, f"send_draft raised: {exc}")
        return {"status": "unknown", "error": str(exc)}

    try:
        update_outreach_status(outreach_id, OUTREACH_SENT)
    except Exception as exc:
        # The email is confirmed sent (message_id in hand) but recording that
        # fact failed. Marking this row 'failed' would be a lie -- it already
        # went out.
        _dead_letter_unknown_outcome(
            outreach_id,
            f"send succeeded (message_id={message_id}) but the status update failed: {exc}",
        )
        return {"status": "unknown", "message_id": message_id, "error": str(exc)}

    return {"status": "success", "message_id": message_id}
