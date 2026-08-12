"""Shared failure handlers for Celery workers.

Two failure shapes recur across workers, and they map to opposite state-machine
outcomes. Centralizing them keeps that distinction consistent everywhere:

  * terminal  — a permanent problem with THIS lead (no email, empty draft, no
    draft to send). Mark it 'failed' so it leaves its current state, drops out
    of the pending_* views, and is never retried.
  * transient — a passing problem (network blip, rate limit). Log it and leave
    the lead's status untouched so the next run naturally retries it.
"""

import logging

from cold_email.workers.shared.db_helpers import record_dead_letter, update_lead_status

logger = logging.getLogger(__name__)


def handle_terminal_failure(lead_id: str, reason: str, *, stage: str, task_name: str) -> None:
    """Mark a lead 'failed' and dead-letter it for later retry.

    Terminal failures leave the lead's current state and land in the DLQ
    (dead_letter table) so they're visible on the lead AND independently
    retryable. `stage`/`task_name` let the DLQ retry re-dispatch to the right
    worker.
    """
    update_lead_status(lead_id, "failed", error_msg=reason)
    record_dead_letter(lead_id, task_name=task_name, stage=stage, error_msg=reason)
    logger.warning(f"Lead {lead_id} marked failed and dead-lettered ({stage}): {reason}")


def handle_transient_failure(lead_id: str, error: Exception | str) -> None:
    """Log a transient failure and leave the lead's status unchanged for retry."""
    logger.error(f"Transient failure on lead {lead_id}: {error}")
