"""Apply migrations/views.sql — the pending_* / available_contacts views.

Run on every boot by start.sh, right after Base.metadata.create_all. SQLAlchemy
metadata does not model views (R23), so a create_all-only database never gets
pending_drafts / pending_sends / available_contacts, and the drafting and
logistics workers that read them would fail every tick with "relation does not
exist". CREATE OR REPLACE VIEW makes this idempotent and self-healing: each
boot re-applies the current definitions with no separate migration step.

Uses the sync engine (not psql — start.sh runs Python) via exec_driver_sql,
which hands the raw multi-statement SQL straight to psycopg2's cursor.execute
rather than SQLAlchemy's text()/bound-parameter path; the latter would choke
on this file's literal ':' -free but still non-trivial SQL, and more
importantly text() is unnecessary here since there are no bind parameters.
"""

import logging
from pathlib import Path

from cold_email.database import sync_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VIEWS_SQL_PATH = Path(__file__).resolve().parent.parent / "migrations" / "views.sql"


def apply_views() -> None:
    """Execute migrations/views.sql against the sync engine. Raises on failure —
    the caller (start.sh) treats this the same as the admin seed: log a
    warning and keep booting, rather than crash-looping the container.
    """
    sql = VIEWS_SQL_PATH.read_text()
    with sync_engine.connect() as conn:
        conn.exec_driver_sql(sql)
        conn.commit()
    logger.info("Applied migrations/views.sql (pending_drafts, pending_sends, available_contacts)")


if __name__ == "__main__":
    apply_views()
