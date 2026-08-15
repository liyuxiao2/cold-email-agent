"""Database helpers for the drafting worker.

Thin, domain-specific wrappers around SQLAlchemy session ops so drafting.py
only holds Celery orchestration. Reads come from the pending_drafts view
(see migrations/006 + migrations/views.sql); writes go to the drafts table +
outreach.status.
"""

import logging

from sqlalchemy import bindparam, text

from cold_email.database import OUTREACH_DRAFTING, OUTREACH_QUEUED, Draft, get_sync_session
from cold_email.workers.shared.views import PendingDraft

logger = logging.getLogger(__name__)

_CLAIM_SQL = text(
    "UPDATE outreach SET status = :drafting, updated_at = now() "
    "WHERE id IN :ids AND status = :queued RETURNING id"
).bindparams(bindparam("ids", expanding=True))


def claim_pending_drafts(outreach_ids: list[str]) -> set[str]:
    """Atomically move the given rows from 'queued' to 'drafting' and return
    the ids ACTUALLY claimed by this call.

    The single UPDATE's own `WHERE status = 'queued'` is the compare-and-swap:
    two concurrent sweeps (a second selection, a manual Regenerate, the hourly
    recovery sweep) can both fetch the same still-queued rows from
    pending_drafts, but only one of them will see a row here once the other
    has already flipped its status — so only one of them ever drafts it.
    Callers must filter their own row list down to the returned ids before
    doing any work.

    Sets updated_at = now() explicitly: this is a raw text() UPDATE, which
    bypasses the ORM unit-of-work entirely, so Outreach.updated_at's
    `onupdate=func.now()` never fires on its own. Without the explicit set
    here, drafting_recovery_task would have no honest way to tell how long a
    row has been claimed, and could never distinguish a claim that just
    started from one whose worker crashed 40 minutes ago.
    """
    if not outreach_ids:
        return set()
    with get_sync_session() as session:
        claimed = (
            session.execute(
                _CLAIM_SQL,
                {"drafting": OUTREACH_DRAFTING, "queued": OUTREACH_QUEUED, "ids": outreach_ids},
            )
            .scalars()
            .all()
        )
        session.commit()
    return {str(outreach_id) for outreach_id in claimed}


def fetch_pending_drafts(user_id: str) -> list[PendingDraft]:
    """Queued outreach rows for ONE user, latest research + contact joined.

    Reading the view — not outreach/research/company_contacts directly — is
    what makes a sweep idempotent: once an outreach row is drafted its status
    changes and it drops out of pending_drafts, so a retried sweep never
    double-drafts it.

    Filtered in the query rather than after fetching: pending_drafts has NO
    built-in user filter of its own (it is `WHERE o.status = 'queued'` across
    every tenant), so a worker drafting another user's row would create it in
    the wrong mailbox with the wrong résumé — exactly the cross-tenant bug
    this parameter exists to prevent drafting_task from reintroducing.
    """
    with get_sync_session() as session:
        rows = (
            session.execute(
                text("SELECT * FROM pending_drafts WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    logger.info(f"{len(rows)} outreach row(s) pending drafting for user {user_id}")
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
