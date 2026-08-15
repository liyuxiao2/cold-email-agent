"""Migration 006 verification.

Runs the real SQL against a seeded fixture resembling production. This is the
highest-value test in the stack: the migration touches every table at once and
runs exactly once against live data.

Unlike every other test file, this one does NOT use the `async_session` fixture.
`async_session` builds the schema from the ORM (`Base.metadata.create_all`) —
i.e. from the POST-migration models, where `leads` no longer exists and
`research.company_id` is already renamed. A migration test whose starting schema
is the finished schema tests nothing. So the fixtures below rebuild the LEGACY
schema by replaying the real historical migration files (001..005) from disk,
which also makes this the only test that exercises the raw SQL migration path.
"""

import pathlib

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DB_URL

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
MIGRATION = MIGRATIONS_DIR / "006_multi_tenant_schema.sql"

# Everything before 006, in the order Postgres saw it in production. Sorted by
# filename, which is how the files are numbered (note two 002_* files).
HISTORICAL_MIGRATIONS = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name < "006")


async def _exec_script(conn, sql: str) -> None:
    """Execute a multi-statement SQL script over an open connection.

    asyncpg sends statements through the extended (prepared) protocol, which
    rejects more than one command per statement. Reaching the driver connection
    directly uses the simple query protocol — what `psql -f` does, and the only
    way to run a migration file verbatim, since semicolon-splitting the text
    would tear apart the `DO $$ ... $$` blocks.
    """
    driver = (await conn.get_raw_connection()).driver_connection
    try:
        await driver.execute(sql)
    except Exception:
        # A failing statement inside the script's own BEGIN leaves the
        # connection in an aborted transaction that SQLAlchemy knows nothing
        # about; clear it so teardown can still reset the schema.
        await driver.execute("ROLLBACK")
        raise


async def _run_script(engine, sql: str) -> None:
    async with engine.connect() as conn:
        await _exec_script(conn, sql)


async def _reset_schema(engine) -> None:
    await _run_script(engine, "DROP SCHEMA public CASCADE; CREATE SCHEMA public;")


async def _run_migration(session) -> None:
    # The session's own connection, so the migration's ALTER TABLEs never wait
    # on a lock held by the seeding transaction.
    await _exec_script(await session.connection(), MIGRATION.read_text())
    await session.commit()


@pytest_asyncio.fixture
async def legacy_engine():
    """A test database holding the pre-006 schema, built from the real SQL.

    Teardown resets the schema so the next test's `async_session` fixture (which
    does create_all/drop_all) starts from empty — otherwise the views created
    here would block its DROP TABLEs.
    """
    engine = create_async_engine(TEST_DB_URL)
    await _reset_schema(engine)
    for path in HISTORICAL_MIGRATIONS:
        await _run_script(engine, path.read_text())
    yield engine
    await _reset_schema(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def legacy_session(legacy_engine):
    factory = async_sessionmaker(legacy_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def admin_user_id(legacy_session):
    """The admin who inherits all backfilled outreach rows.

    Inserted as SQL, not via the ORM `User` model: this file deliberately knows
    only the historical schema.
    """
    user_id = (
        await legacy_session.execute(
            text("""
            INSERT INTO users (email, google_sub, role)
            VALUES ('admin@example.com', 'sub-admin', 'admin')
            RETURNING id
            """)
        )
    ).scalar_one()
    await legacy_session.commit()
    return user_id


@pytest_asyncio.fixture
async def legacy_fixture(legacy_session, admin_user_id):
    """Seed a pre-migration database resembling production.

    Deliberately covers every status and both the has-email and no-email cases,
    because the backfill branches on exactly those.
    """
    await legacy_session.execute(
        text("""
        INSERT INTO leads (id, company_name, founder_name, founder_email, company_url, status)
        VALUES
          ('11111111-1111-1111-1111-111111111111', 'FoundCo',    NULL,        NULL,
           'https://found.co',    'found'),
          ('22222222-2222-2222-2222-222222222222', 'ResearchCo', 'Ann Reed',  'ann@research.co',
           'https://research.co', 'researched'),
          ('33333333-3333-3333-3333-333333333333', 'DraftCo',    'Bo Lin',    'bo@draft.co',
           'https://draft.co',    'drafted'),
          ('44444444-4444-4444-4444-444444444444', 'SentCo',     'Cy Ode',    'cy@sent.co',
           'https://sent.co',     'sent'),
          ('55555555-5555-5555-5555-555555555555', 'NoEmailCo',  'Dee Ray',   NULL,
           'https://noemail.co',  'failed'),
          ('66666666-6666-6666-6666-666666666666', 'DraftFailCo','Eli Poe',   'eli@draftfail.co',
           'https://draftfail.co','failed'),
          ('77777777-7777-7777-7777-777777777777', 'OneWordCo',  'Prince',    'p@oneword.co',
           'https://oneword.co',  'researched')
        """)
    )
    await legacy_session.execute(
        text("""
        INSERT INTO research (lead_id, tech_stack, recent_news, hook, raw_content)
        SELECT id, '["python"]'::jsonb, 'news', 'hook', 'raw'
        FROM leads WHERE status <> 'found'
        """)
    )
    await legacy_session.execute(
        text("""
        INSERT INTO drafts (lead_id, subject_line, body, gmail_draft_id)
        VALUES
          ('33333333-3333-3333-3333-333333333333', 'Hi DraftCo', 'body', 'gd-1'),
          ('44444444-4444-4444-4444-444444444444', 'Hi SentCo',  'body', 'gd-2')
        """)
    )
    await legacy_session.execute(
        text("""
        INSERT INTO dead_letter (lead_id, task_name, stage, error_msg)
        VALUES
          ('55555555-5555-5555-5555-555555555555', 'research_task', 'research',  'no email'),
          ('66666666-6666-6666-6666-666666666666', 'drafting_task', 'drafting',  'empty draft')
        """)
    )
    await legacy_session.commit()


@pytest_asyncio.fixture
async def legacy_fixture_no_admin(legacy_session):
    """Same shape, but with no admin user — the migration must abort."""
    await legacy_session.execute(
        text("""
        INSERT INTO leads (id, company_name, founder_email, status)
        VALUES ('88888888-8888-8888-8888-888888888888', 'OrphanCo', 'x@orphan.co', 'researched')
        """)
    )
    await legacy_session.commit()


@pytest.mark.asyncio
async def test_creates_the_three_tables(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    for table in ("companies", "company_contacts", "outreach"):
        result = await legacy_session.execute(
            text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}
        )
        assert result.scalar_one() is True, f"{table} missing"


@pytest.mark.asyncio
async def test_leads_is_renamed_not_dropped(legacy_fixture, legacy_session):
    """A bad deploy must be recoverable without restoring a backup."""
    await _run_migration(legacy_session)
    assert (
        await legacy_session.execute(text("SELECT to_regclass('leads_legacy') IS NOT NULL"))
    ).scalar_one() is True
    assert (
        await legacy_session.execute(text("SELECT to_regclass('leads') IS NULL"))
    ).scalar_one() is True


@pytest.mark.asyncio
async def test_outreach_unique_user_company(legacy_fixture, legacy_session, admin_user_id):
    await _run_migration(legacy_session)
    company_id = (
        await legacy_session.execute(text("SELECT id FROM companies LIMIT 1"))
    ).scalar_one()

    with pytest.raises(IntegrityError, match="duplicate key value"):
        await legacy_session.execute(
            text(
                "INSERT INTO outreach (user_id, company_id, status) VALUES (:u, :c, 'queued'), "
                "(:u, :c, 'queued')"
            ),
            {"u": admin_user_id, "c": company_id},
        )
        await legacy_session.commit()


@pytest.mark.asyncio
async def test_dead_letter_requires_one_level(legacy_fixture, legacy_session):
    """The CHECK constraint prevents a dead-letter row belonging to neither a
    company nor an outreach row — an unretryable orphan."""
    await _run_migration(legacy_session)
    with pytest.raises(IntegrityError, match="dead_letter_one_level"):
        await legacy_session.execute(
            text(
                "INSERT INTO dead_letter (task_name, stage, error_msg) "
                "VALUES ('t', 'research', 'e')"
            )
        )
        await legacy_session.commit()


@pytest.mark.asyncio
async def test_a_company_discovered_after_the_migration_is_usable(legacy_fixture, legacy_session):
    """Guards the stale-FK trap: research.lead_id's old foreign key pointed at
    `leads`, and a rename would have left it pointing at the frozen
    leads_legacy, rejecting every company created from then on."""
    await _run_migration(legacy_session)
    company_id = (
        await legacy_session.execute(
            text("INSERT INTO companies (company_name) VALUES ('NewCo') RETURNING id")
        )
    ).scalar_one()
    await legacy_session.execute(
        text("INSERT INTO research (company_id, hook) VALUES (:c, 'fresh hook')"),
        {"c": company_id},
    )
    await legacy_session.commit()


@pytest.mark.asyncio
async def test_aborts_without_an_admin(legacy_session, legacy_fixture_no_admin):
    """Without an admin there is nobody to own the backfilled outreach rows."""
    # Raised by the guard's RAISE EXCEPTION, straight from the driver: the
    # script runs on the raw connection, so SQLAlchemy never wraps it.
    with pytest.raises(asyncpg.exceptions.RaiseError, match="No admin user exists"):
        await _run_migration(legacy_session)
