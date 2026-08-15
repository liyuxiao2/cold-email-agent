"""Apply migrations/storage.sql — column storage strategies create_all can't express.

Run on every boot by start.sh, right after Base.metadata.create_all (and after
scripts/apply_views.py, though order between the two does not matter — each
file is independent and idempotent). SQLAlchemy's Column API has no way to set
a column's TOAST storage strategy (R32), so a create_all-only database leaves
profiles.resume_pdf at the default EXTENDED strategy, and Postgres spends CPU
attempting to compress every PDF write for no size gain.

SQL_FILES is a list, not a single path, so a later stack that hits the same
class of gap (another column needing a non-default storage strategy, a
FILLFACTOR tweak, etc.) can add a file here instead of inventing a third
mechanism. Every file in the list must be safe to run unconditionally on every
boot — `ALTER ... SET STORAGE` already is, since setting the same strategy
twice is a no-op.

Uses the sync engine (not psql — start.sh runs Python) via exec_driver_sql, the
same as apply_views.py, for the same reason: no bind parameters, and this
hands the raw SQL straight to psycopg2's cursor rather than SQLAlchemy's
text()/bound-parameter path.
"""

import logging
from pathlib import Path

from cold_email.database import sync_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
SQL_FILES = (MIGRATIONS_DIR / "storage.sql",)


def apply_storage() -> None:
    """Execute each file in SQL_FILES against the sync engine. Raises on
    failure — the caller (start.sh) treats this the same as the admin seed and
    view provisioning: log a warning and keep booting, rather than
    crash-looping the container.
    """
    with sync_engine.connect() as conn:
        for path in SQL_FILES:
            conn.exec_driver_sql(path.read_text())
        conn.commit()
    logger.info("Applied post-create_all storage DDL: %s", ", ".join(p.name for p in SQL_FILES))


if __name__ == "__main__":
    apply_storage()
