"""Database helpers for the logistics worker.

Reads from the pending_sends view (approved leads + their latest draft), writes
leads.status. Keeps logistics.py to Celery orchestration only.
"""

import logging

from sqlalchemy import text

from cold_email.database import Lead, get_sync_session

logger = logging.getLogger(__name__)


def fetch_send_inputs(lead_id: str) -> dict | None:
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
    return dict(row) if row else None


def update_lead_status(lead_id: str, status: str, error_msg: str | None = None) -> None:
    """Update the status (and optional error message) of a lead."""
    with get_sync_session() as session:
        db_lead = session.get(Lead, lead_id)
        if db_lead:
            db_lead.status = status
            if error_msg is not None:
                db_lead.error_msg = error_msg
            session.commit()
            logger.info(f"Lead {lead_id} status updated to {status!r}")
