"""Apply post-create_all DDL that Base.metadata.create_all cannot express or
will not apply to a table that already exists.

Run on every boot by start.sh, right after Base.metadata.create_all (and after
scripts/apply_views.py, though order between the two does not matter — each
file is independent and idempotent). Two distinct gaps land here:

  * Column storage strategy (R32): SQLAlchemy's Column API has no way to set
    a column's TOAST storage strategy, so a create_all-only database leaves
    profiles.resume_pdf at the default EXTENDED strategy, and Postgres spends
    CPU attempting to compress every PDF write for no size gain
    (migrations/storage.sql).
  * ALTER TABLE on an existing table (R43): create_all only ever issues
    CREATE TABLE — it never alters a table that already exists. A migration
    that adds columns to `users` (008_user_llm_and_quota.sql,
    010_send_cadence.sql) or `outreach` (009_outreach_reclaim_count.sql,
    010_send_cadence.sql's new indexes) is therefore invisible to create_all
    on any database that already has that table, which in production is
    every deploy after the first. Written entirely as `ADD COLUMN IF NOT
    EXISTS` / `CREATE INDEX IF NOT EXISTS`, so it's as safe to run on every
    boot as the storage DDL. Migration 008 was originally missed here, which
    would have produced a full authenticated-app outage that /api/health
    reported as healthy (health only counts Company, never users) — do not
    repeat that gap for a new ALTER TABLE.

SQL_FILES is a list, not a single path, so a later stack that hits either
class of gap (another column needing a non-default storage strategy, a
FILLFACTOR tweak, another ALTER TABLE ADD COLUMN on an existing table, etc.)
can add a file here instead of inventing a third mechanism. Every file in the
list must be safe to run unconditionally on every boot — `ALTER ... SET
STORAGE` and `ADD COLUMN IF NOT EXISTS` both already are, since re-applying
either is a no-op.

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
SQL_FILES = (
    MIGRATIONS_DIR / "storage.sql",
    MIGRATIONS_DIR / "008_user_llm_and_quota.sql",
    MIGRATIONS_DIR / "009_outreach_reclaim_count.sql",
    MIGRATIONS_DIR / "010_send_cadence.sql",
)


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
