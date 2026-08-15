"""R32/R43 — post-create_all DDL that Base.metadata.create_all can't apply.

scripts/start.sh provisions the schema with Base.metadata.create_all, which
has two gaps this module's tests cover:

  * R32: SQLAlchemy's Column API cannot express Postgres's TOAST storage
    strategy, so profiles.resume_pdf never gets SET STORAGE EXTERNAL unless
    migrations/storage.sql is separately applied.
  * R43: create_all only ever issues CREATE TABLE — it never ALTERs a table
    that already exists. migrations/008_user_llm_and_quota.sql adds three
    columns to `users`, which is invisible to create_all on any database
    where `users` predates this stack (i.e. every production deploy).

Both are applied by scripts/apply_storage.py, run by start.sh right after
create_all, via the same SQL_FILES list.

Deliberately does NOT call scripts.apply_storage.apply_storage(): that function
is bound to cold_email.database.sync_engine, i.e. the PRODUCTION database URL.
This test drives its own sync engine against cold_email_test instead, the same
pattern tests/test_views.py uses.
"""

import pathlib

import sqlalchemy
from sqlalchemy import text

from tests.conftest import TEST_DB_URL

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
STORAGE_SQL_PATH = MIGRATIONS_DIR / "storage.sql"
USER_LLM_AND_QUOTA_SQL_PATH = MIGRATIONS_DIR / "008_user_llm_and_quota.sql"


def test_resume_pdf_column_is_storage_external(async_session):
    """SELECT attstorage ... should be 'e' (EXTERNAL), not the default 'x' (EXTENDED)."""
    sync_engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    try:
        with sync_engine.connect() as conn:
            conn.exec_driver_sql(STORAGE_SQL_PATH.read_text())
            conn.commit()

        with sync_engine.connect() as conn:
            attstorage = conn.execute(
                text(
                    "SELECT attstorage FROM pg_attribute "
                    "WHERE attrelid = 'profiles'::regclass AND attname = 'resume_pdf'"
                )
            ).scalar_one()
        assert attstorage == "e"
    finally:
        sync_engine.dispose()


def test_applying_storage_sql_twice_does_not_raise(async_session):
    """ALTER ... SET STORAGE is idempotent, so re-applying on every boot is safe."""
    sync_engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    try:
        with sync_engine.connect() as conn:
            conn.exec_driver_sql(STORAGE_SQL_PATH.read_text())
            conn.exec_driver_sql(STORAGE_SQL_PATH.read_text())
            conn.commit()
    finally:
        sync_engine.dispose()


def test_apply_storage_provisions_migration_008():
    """R43: scripts.apply_storage.SQL_FILES must include 008 — the ONLY thing
    in the boot sequence that can put llm_api_key_enc / llm_provider /
    monthly_draft_quota onto a `users` table that predates this stack, since
    create_all only CREATEs tables and never ALTERs an existing one."""
    from scripts.apply_storage import SQL_FILES

    assert USER_LLM_AND_QUOTA_SQL_PATH in SQL_FILES


def test_database_missing_the_byok_columns_gets_them_after_apply_storage(async_session):
    """The exact scenario the fix addresses: a production `users` table
    provisioned BEFORE this stack (so it has none of the three BYOK/quota
    columns, since create_all only creates missing tables and never alters an
    existing one) must end up with all three after the post-create_all DDL
    mechanism runs — not just on a fresh database that never needed it.
    """
    sync_engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    try:
        with sync_engine.connect() as conn:
            # Simulate the pre-008 production schema: drop the columns this
            # migration adds, so `users` looks like it was provisioned by an
            # older version of the ORM model / an older create_all run.
            conn.exec_driver_sql(
                "ALTER TABLE users "
                "DROP COLUMN IF EXISTS llm_api_key_enc, "
                "DROP COLUMN IF EXISTS llm_provider, "
                "DROP COLUMN IF EXISTS monthly_draft_quota"
            )
            conn.commit()

            missing = (
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name IN "
                        "('llm_api_key_enc', 'llm_provider', 'monthly_draft_quota')"
                    )
                )
                .scalars()
                .all()
            )
            assert missing == []

            # The fix: this is the exact file scripts/apply_storage.py now
            # runs on every boot.
            conn.exec_driver_sql(USER_LLM_AND_QUOTA_SQL_PATH.read_text())
            conn.commit()

            present = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name IN "
                        "('llm_api_key_enc', 'llm_provider', 'monthly_draft_quota')"
                    )
                )
                .scalars()
                .all()
            )
            assert present == {"llm_api_key_enc", "llm_provider", "monthly_draft_quota"}

            quota_default = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'monthly_draft_quota'"
                )
            ).scalar_one()
            assert quota_default == "100"
    finally:
        # Restore the columns so this test doesn't leave the shared test
        # database's `users` table permanently missing them for later tests
        # in the same session (create_all only creates MISSING tables, so a
        # dropped column on an existing table would otherwise stay dropped).
        with sync_engine.connect() as conn:
            conn.exec_driver_sql(USER_LLM_AND_QUOTA_SQL_PATH.read_text())
            conn.commit()
        sync_engine.dispose()
