"""Shared failure handlers for Celery workers.

Two failure shapes recur, mapping to opposite state-machine outcomes:

  * terminal  — a permanent problem. Mark the entity 'failed' so it leaves its
    current state, drops out of the pending_* views, and is not retried. Also
    write a DLQ row so it stays independently retryable.
  * transient — a passing problem (network blip, rate limit). Log it and leave
    the status untouched so the next run retries naturally.

After the tenancy split, terminal failure needs TWO entry points because the two
levels update different tables and mean different things:

  * fail_company  — nobody can email this company (research found no contacts)
  * fail_outreach — this user's draft or send broke

One function with a nullable company_id/outreach_id pair would push the branch
into every call site and make the CHECK constraint reachable by accident.
"""

import logging

from cold_email.database import OUTREACH_FAILED, RESEARCH_FAILED
from cold_email.workers.shared.db_helpers import (
    record_dead_letter,
    set_outreach_error_msg,
    update_company_research_status,
    update_outreach_status,
)

logger = logging.getLogger(__name__)


def fail_company(company_id: str, reason: str, *, stage: str, task_name: str) -> None:
    """Terminal failure at the GLOBAL level: this company is not emailable."""
    update_company_research_status(company_id, RESEARCH_FAILED, error_msg=reason)
    record_dead_letter(company_id=company_id, task_name=task_name, stage=stage, error_msg=reason)
    logger.warning(f"Company {company_id} failed and dead-lettered ({stage}): {reason}")


def fail_outreach(outreach_id: str, reason: str, *, stage: str, task_name: str) -> None:
    """Terminal failure at the PER-USER level: this user's outreach broke."""
    update_outreach_status(outreach_id, OUTREACH_FAILED, error_msg=reason)
    record_dead_letter(outreach_id=outreach_id, task_name=task_name, stage=stage, error_msg=reason)
    logger.warning(f"Outreach {outreach_id} failed and dead-lettered ({stage}): {reason}")


def handle_transient_failure(outreach_id: str, error: Exception | str) -> None:
    """Log a transient failure and record it on the row, leaving STATUS
    untouched so the next run retries naturally.

    Before this, a transient failure (a rate-limit chain exhaustion, a
    provider blip) left the row with no error_msg and no DLQ row — nothing a
    user could see — while the hourly recovery sweep silently retried the
    same doomed row forever. Writing the reason to outreach.error_msg makes
    that state explicable in the UI and the database without making it
    terminal: no DLQ row, no status change.
    """
    logger.error(f"Transient failure on {outreach_id}: {error}")
    set_outreach_error_msg(outreach_id, str(error))
