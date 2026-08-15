"""R23 — production has no database VIEWS from a plain schema provision.

scripts/start.sh provisions the schema with Base.metadata.create_all, and
SQLAlchemy's metadata does not model views at all, so pending_drafts /
pending_sends / available_contacts never exist on a create_all-only database
unless migrations/views.sql is separately applied (see scripts/apply_views.py,
run by start.sh right after create_all). This test proves that combination —
create_all, then views.sql — actually produces three selectable views.

Deliberately does NOT call scripts.apply_views.apply_views(): that function is
bound to cold_email.database.sync_engine, i.e. the PRODUCTION database URL.
This test drives its own sync engine against cold_email_test instead, the same
pattern tests/conftest.py's sync_session_for fixture uses to bridge into the
async test transaction.
"""

import pathlib

import pytest
import sqlalchemy
from sqlalchemy import text

from tests.conftest import TEST_DB_URL

VIEWS_SQL_PATH = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "views.sql"
VIEW_NAMES = ("pending_drafts", "pending_sends", "available_contacts")


@pytest.mark.asyncio
async def test_views_exist_and_are_selectable_after_create_all(async_session):
    """create_all (already done by the async_session fixture) + views.sql must
    yield three selectable views — the exact sequence scripts/start.sh runs.
    """
    sync_engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    try:
        with sync_engine.connect() as conn:
            conn.exec_driver_sql(VIEWS_SQL_PATH.read_text())
            conn.commit()

        with sync_engine.connect() as conn:
            for view in VIEW_NAMES:
                # Raises ProgrammingError (relation does not exist) if the view
                # was never created, or if it referenced a column that create_all's
                # tables don't actually have. `view` is our own hardcoded
                # VIEW_NAMES tuple, never external input.
                conn.execute(text(f"SELECT * FROM {view} LIMIT 1"))  # noqa: S608
    finally:
        # Must run before async_session's own teardown drops the underlying
        # tables — Postgres refuses DROP TABLE while a view still depends on it.
        with sync_engine.connect() as conn:
            for view in VIEW_NAMES:
                conn.exec_driver_sql(f"DROP VIEW IF EXISTS {view} CASCADE")
            conn.commit()
        sync_engine.dispose()


def test_views_sql_matches_migration_006_definitions():
    """Guards the "copy them; do not re-derive them" instruction: the two
    provisioning paths (create_all+views.sql, and the migration files) must
    stay byte-identical in their view bodies, or they will silently diverge.
    """
    views_sql = VIEWS_SQL_PATH.read_text()
    migration_sql = (
        pathlib.Path(__file__).resolve().parent.parent
        / "migrations"
        / "006_multi_tenant_schema.sql"
    ).read_text()

    for view in VIEW_NAMES:
        # Extract "<view> AS\n...;" bodies and compare, ignoring the
        # CREATE [OR REPLACE] VIEW prefix which legitimately differs.
        views_body = views_sql.split(f"VIEW {view} AS", 1)[1].split(";", 1)[0]
        migration_body = migration_sql.split(f"VIEW {view} AS", 1)[1].split(";", 1)[0]
        assert views_body == migration_body, f"{view} definition has drifted between the two files"
