"""R32 — production has no column storage strategy from a plain schema provision.

scripts/start.sh provisions the schema with Base.metadata.create_all, and
SQLAlchemy's Column API cannot express Postgres's TOAST storage strategy, so
profiles.resume_pdf never gets SET STORAGE EXTERNAL unless migrations/storage.sql
is separately applied (see scripts/apply_storage.py, run by start.sh right
after create_all). This test proves that combination — create_all, then
storage.sql — actually flips the column's storage strategy.

Deliberately does NOT call scripts.apply_storage.apply_storage(): that function
is bound to cold_email.database.sync_engine, i.e. the PRODUCTION database URL.
This test drives its own sync engine against cold_email_test instead, the same
pattern tests/test_views.py uses.
"""

import pathlib

import sqlalchemy
from sqlalchemy import text

from tests.conftest import TEST_DB_URL

STORAGE_SQL_PATH = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "storage.sql"


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
