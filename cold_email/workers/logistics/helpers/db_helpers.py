"""Database helpers for the logistics worker.

Reads from the pending_sends view (approved outreach rows + their latest
draft), writes outreach.status. Keeps logistics.py to Celery orchestration
only.
"""

import logging

from sqlalchemy import text

from cold_email.database import Outreach, User, get_sync_session
from cold_email.workers.shared.views import PendingSend

logger = logging.getLogger(__name__)


def fetch_send_inputs(outreach_id: str) -> PendingSend | None:
    """Return the pending_sends row for one outreach, or None if it isn't sendable.

    A missing row means either the outreach isn't in the 'approved' state
    (already sent, rejected, or never approved), or it has no draft yet — the
    same existence check that gives us idempotency.
    """
    with get_sync_session() as session:
        row = (
            session.execute(
                text("SELECT * FROM pending_sends WHERE outreach_id = :outreach_id"),
                {"outreach_id": outreach_id},
            )
            .mappings()
            .first()
        )
    return PendingSend(**row) if row else None


def fetch_outreach_status(outreach_id: str) -> str | None:
    """Return the outreach row's own current status, or None if it doesn't exist.

    pending_sends alone can't distinguish "not our turn yet" (queued/drafted/
    sent/rejected — an idempotent no-op) from "approved but nothing to send"
    (a real anomaly worth failing): a missing view row means "absent" either
    way. This lets the caller tell the two apart.
    """
    with get_sync_session() as session:
        outreach = session.get(Outreach, outreach_id)
        return outreach.status if outreach else None


def fetch_owning_user(user_id: str) -> User | None:
    """Return the User row that owns an outreach row, or None if it no longer exists.

    Sending must always use the OWNING user's mailbox, never a global or the
    caller's — this is the only place logistics_task looks up a user.
    """
    with get_sync_session() as session:
        return session.get(User, user_id)
