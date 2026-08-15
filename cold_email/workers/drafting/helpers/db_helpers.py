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


def fetch_pending_drafts() -> list[PendingDraft]:
    """Return every outreach row ready to be drafted (status='queued'), latest
    research + contact joined.

    Reading the view — not outreach/research/company_contacts directly — is
    what makes the batch sweep idempotent: once an outreach row is drafted its
    status changes and it drops out of pending_drafts, so a retried sweep
    never double-drafts it.
    """
    with get_sync_session() as session:
        rows = session.execute(text("SELECT * FROM pending_drafts")).mappings().all()
    logger.info(f"{len(rows)} outreach row(s) pending drafting")
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
