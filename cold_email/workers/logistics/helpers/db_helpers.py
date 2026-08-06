"""Database helpers for the logistics worker.

Reads from the pending_sends view (approved leads + their latest draft), writes
leads.status. Keeps logistics.py to Celery orchestration only.
"""

import logging

from sqlalchemy import text

from cold_email.database import get_sync_session
from cold_email.workers.views import PendingSend

logger = logging.getLogger(__name__)


def fetch_send_inputs(lead_id: str) -> PendingSend | None:
    """Return the pending_sends row for one lead, or None if it isn't sendable.

    A missing row means the lead isn't in the 'approved' state (already sent, or
    never approved) — the same existence check that gives us idempotency.
    """
    with get_sync_session() as session:
        row = (
            session.execute(
                text("SELECT * FROM pending_sends WHERE lead_id = :lead_id"),
                {"lead_id": lead_id},
            )
            .mappings()
            .first()
        )
    return PendingSend(**row) if row else None
