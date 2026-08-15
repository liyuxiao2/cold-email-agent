"""Database helpers for the drafting worker.

Thin, domain-specific wrappers around SQLAlchemy session ops so drafting.py
only holds Celery orchestration. Reads come from the pending_drafts view
(see migrations/006 + migrations/views.sql); writes go to the drafts table +
outreach.status.
"""

import logging

from sqlalchemy import text

from cold_email.database import Draft, get_sync_session
from cold_email.workers.shared.views import PendingDraft

logger = logging.getLogger(__name__)


def fetch_pending_user_ids() -> list[str]:
    """Distinct user_ids with at least one queued outreach row.

    The sweep is per-user (see drafting_task): this is the "which users have
    work" step, run once per sweep, before any per-user SenderContext load.
    """
    with get_sync_session() as session:
        rows = session.execute(text("SELECT DISTINCT user_id FROM pending_drafts")).all()
    return [row[0] for row in rows]


def fetch_pending_drafts(user_id: str | None = None) -> list[PendingDraft]:
    """Return outreach rows ready to be drafted (status='queued'), latest
    research + contact joined.

    Reading the view — not outreach/research/company_contacts directly — is
    what makes the batch sweep idempotent: once an outreach row is drafted its
    status changes and it drops out of pending_drafts, so a retried sweep
    never double-drafts it.

    `user_id` scopes the read to one tenant's rows. pending_drafts has NO
    built-in user filter (it is `WHERE o.status = 'queued'` across every
    user), so a caller that wants only one user's work MUST pass user_id —
    omitting it returns every user's queued rows, which is exactly the
    cross-tenant bug this parameter exists to prevent drafting_task from
    reintroducing.
    """
    query = "SELECT * FROM pending_drafts"
    params: dict = {}
    if user_id is not None:
        query += " WHERE user_id = :user_id"
        params["user_id"] = user_id

    with get_sync_session() as session:
        rows = session.execute(text(query), params).mappings().all()
    scope = f" for user {user_id}" if user_id else ""
    logger.info(f"{len(rows)} outreach row(s) pending drafting{scope}")
    return [PendingDraft(**row) for row in rows]


def commit_draft(
    outreach_id: str,
    subject_line: str,
    body: str,
    gmail_draft_id: str,
) -> None:
    """Insert a new Draft row for the given outreach."""
    with get_sync_session() as session:
        session.add(
            Draft(
                outreach_id=outreach_id,
                subject_line=subject_line,
                body=body,
                gmail_draft_id=gmail_draft_id,
            )
        )
        session.commit()
        logger.info(f"Draft for outreach {outreach_id} saved (gmail_draft_id={gmail_draft_id})")
