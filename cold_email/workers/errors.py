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

from cold_email.workers.db_helpers import update_lead_status

logger = logging.getLogger(__name__)


def handle_terminal_failure(lead_id: str, reason: str) -> None:
    """Mark a lead 'failed' with a reason; it exits the pipeline for good."""
    update_lead_status(lead_id, "failed", error_msg=reason)
    logger.warning(f"Lead {lead_id} marked failed: {reason}")


def handle_transient_failure(lead_id: str, error: Exception | str) -> None:
    """Log a transient failure and leave the lead's status unchanged for retry."""
    logger.error(f"Transient failure on lead {lead_id}: {error}")
