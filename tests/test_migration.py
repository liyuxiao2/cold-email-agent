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

from cold_email.database import Base
from tests.conftest import TEST_DB_URL

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
MIGRATION = MIGRATIONS_DIR / "006_multi_tenant_schema.sql"
# R33: 007 adds `profiles`, which must be provisioned on the migration-built
# side too, or test_create_all_and_the_migration_agree_on_indexes_and_constraints
# would compare schemas of different vintage. Extend this list — not just
# MIGRATION — as later stacks add migrations that need parity coverage.
MIGRATIONS = (MIGRATION, MIGRATIONS_DIR / "007_profiles.sql")

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


# The tables MIGRATIONS creates or rewrites (006 + 007). Both provisioning
# paths — these files and Base.metadata.create_all — must produce identical
# indexes and constraints on all of them.
CONVERGING_TABLES = (
    "companies",
    "company_contacts",
    "outreach",
    "research",
    "drafts",
    "dead_letter",
    "profiles",
)


async def _schema_fingerprint(engine) -> dict:
    """Every index definition and constraint on the tables 006 touches."""
    async with engine.connect() as conn:
        indexes = (
            await conn.execute(
                text("""
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = ANY(:tables)
                ORDER BY indexname
                """),
                {"tables": list(CONVERGING_TABLES)},
            )
        ).all()
        constraints = (
            await conn.execute(
                text("""
                SELECT cl.relname, con.conname, con.contype
                FROM pg_constraint con
                JOIN pg_class cl ON cl.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE n.nspname = 'public' AND cl.relname = ANY(:tables)
                ORDER BY cl.relname, con.conname
                """),
                {"tables": list(CONVERGING_TABLES)},
            )
        ).all()
    return {
        "indexes": {name: definition for name, definition in indexes},
        "constraints": {(t, name, kind) for t, name, kind in constraints},
    }


async def _run_migration(session) -> None:
    # The session's own connection, so the migration's ALTER TABLEs never wait
    # on a lock held by the seeding transaction. Runs every file in MIGRATIONS,
    # not just 006, so the parity test below stays honest (R33) — profiles
    # (007) must exist on this side or it can never be compared against
    # create_all's version of the same table.
    conn = await session.connection()
    for path in MIGRATIONS:
        await _exec_script(conn, path.read_text())
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
           'https://oneword.co',  'researched'),
          ('88888888-8888-8888-8888-888888888888', 'ApprovedCo', 'Gia Bell',  'gia@approved.co',
           'https://approved.co', 'approved'),
          ('99999999-9999-9999-9999-999999999999', 'RejectedCo', 'Hal Fox',   'hal@rejected.co',
           'https://rejected.co', 'rejected')
        """)
    )
    # RejectedCo's error_msg is a REVIEWER NOTE, not a failure message: the old
    # reject handler (POST /api/leads/{id}/reject) wrote `lead.error_msg =
    # payload.notes`. This is exactly the case the companies backfill must NOT
    # carry forward — it's a private per-user note, not a global company fact.
    await legacy_session.execute(
        text("""
        UPDATE leads SET error_msg = 'founder was rude on the call'
        WHERE id = '99999999-9999-9999-9999-999999999999'
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company,expected",
    [
        ("FoundCo", "found"),
        ("ResearchCo", "researched"),
        ("DraftCo", "researched"),
        ("SentCo", "researched"),
        ("NoEmailCo", "failed"),  # failed with no email = research failed
        ("DraftFailCo", "researched"),  # failed WITH an email = drafting failed
    ],
)
async def test_research_status_mapping(legacy_fixture, legacy_session, company, expected):
    await _run_migration(legacy_session)
    status = (
        await legacy_session.execute(
            text("SELECT research_status FROM companies WHERE company_name = :n"), {"n": company}
        )
    ).scalar_one()
    assert status == expected


@pytest.mark.asyncio
async def test_reviewer_note_does_not_leak_into_the_global_company_row(
    legacy_fixture, legacy_session
):
    """RejectedCo's leads.error_msg is a REVIEWER NOTE ('founder was rude on
    the call'), not a failure message — the old reject handler wrote
    lead.error_msg = payload.notes. Backfilling it onto companies.error_msg
    would turn a private per-user note into a fact GET /api/companies serves
    to every signup. The note belongs on outreach.error_msg (per-user) only."""
    await _run_migration(legacy_session)
    row = (
        await legacy_session.execute(
            text("""
            SELECT c.error_msg AS company_error_msg, o.error_msg AS outreach_error_msg
            FROM companies c
            JOIN outreach o ON o.company_id = c.id
            WHERE c.company_name = 'RejectedCo'
            """)
        )
    ).one()
    assert row.company_error_msg is None
    assert row.outreach_error_msg == "founder was rude on the call"


@pytest.mark.asyncio
async def test_company_ids_are_preserved(legacy_fixture, legacy_session):
    """The trick the whole migration rests on."""
    await _run_migration(legacy_session)
    row = (
        await legacy_session.execute(
            text("SELECT id FROM companies WHERE company_name = 'ResearchCo'")
        )
    ).scalar_one()
    assert str(row) == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_founder_email_becomes_an_eligible_founder_contact(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    row = (
        await legacy_session.execute(
            text("""
            SELECT ct.email, ct.first_name, ct.last_name, ct.is_founder,
                   ct.eligible, ct.confidence
            FROM company_contacts ct
            JOIN companies c ON c.id = ct.company_id
            WHERE c.company_name = 'ResearchCo'
            """)
        )
    ).one()
    assert row.email == "ann@research.co"
    assert row.first_name == "Ann"
    assert row.last_name == "Reed"
    assert row.is_founder is True
    assert row.eligible is True
    assert row.confidence == 25  # sentinel: Hunter's real score was never stored


@pytest.mark.asyncio
async def test_single_word_founder_name_gets_no_fabricated_surname(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    row = (
        await legacy_session.execute(
            text("""
            SELECT ct.first_name, ct.last_name FROM company_contacts ct
            JOIN companies c ON c.id = ct.company_id WHERE c.company_name = 'OneWordCo'
            """)
        )
    ).one()
    assert row.first_name == "Prince"
    assert row.last_name is None


@pytest.mark.asyncio
async def test_lead_without_an_email_gets_no_contact_and_no_outreach(
    legacy_fixture, legacy_session
):
    await _run_migration(legacy_session)
    counts = (
        await legacy_session.execute(
            text("""
            SELECT
              (SELECT COUNT(*) FROM company_contacts ct JOIN companies c ON c.id = ct.company_id
                WHERE c.company_name = 'NoEmailCo') AS contacts,
              (SELECT COUNT(*) FROM outreach o JOIN companies c ON c.id = o.company_id
                WHERE c.company_name = 'NoEmailCo') AS outreach
            """)
        )
    ).one()
    assert counts.contacts == 0
    assert counts.outreach == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company,status",
    [("DraftCo", "drafted"), ("SentCo", "sent"), ("DraftFailCo", "failed")],
)
async def test_outreach_status_carried_over(legacy_fixture, legacy_session, company, status):
    await _run_migration(legacy_session)
    got = (
        await legacy_session.execute(
            text("""
            SELECT o.status FROM outreach o JOIN companies c ON c.id = o.company_id
            WHERE c.company_name = :n
            """),
            {"n": company},
        )
    ).scalar_one()
    assert got == status


@pytest.mark.asyncio
async def test_outreach_is_owned_by_the_admin(legacy_fixture, legacy_session, admin_user_id):
    await _run_migration(legacy_session)
    owners = (
        (await legacy_session.execute(text("SELECT DISTINCT user_id FROM outreach")))
        .scalars()
        .all()
    )
    assert owners == [admin_user_id]


@pytest.mark.asyncio
async def test_research_rows_still_resolve_to_their_company(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    hook = (
        await legacy_session.execute(
            text("""
            SELECT r.hook FROM research r JOIN companies c ON c.id = r.company_id
            WHERE c.company_name = 'ResearchCo'
            """)
        )
    ).scalar_one()
    assert hook == "hook"


@pytest.mark.asyncio
async def test_drafts_point_at_the_right_outreach_row(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    subject = (
        await legacy_session.execute(
            text("""
            SELECT d.subject_line FROM drafts d
            JOIN outreach o  ON o.id = d.outreach_id
            JOIN companies c ON c.id = o.company_id
            WHERE c.company_name = 'DraftCo'
            """)
        )
    ).scalar_one()
    assert subject == "Hi DraftCo"


@pytest.mark.asyncio
async def test_dead_letter_rows_land_on_the_right_level(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    rows = (
        await legacy_session.execute(
            text("SELECT stage, company_id, outreach_id FROM dead_letter ORDER BY stage")
        )
    ).all()
    by_stage = {r.stage: r for r in rows}
    assert by_stage["research"].company_id is not None
    assert by_stage["research"].outreach_id is None
    assert by_stage["drafting"].outreach_id is not None


@pytest.mark.asyncio
async def test_legacy_table_retains_every_row(legacy_fixture, legacy_session):
    await _run_migration(legacy_session)
    assert (
        await legacy_session.execute(text("SELECT COUNT(*) FROM leads_legacy"))
    ).scalar_one() == 9


@pytest.mark.asyncio
async def test_a_researched_lead_with_a_leftover_draft_gets_an_outreach_row_at_drafted(
    legacy_fixture, legacy_session
):
    """The old POST /api/leads/{id}/regenerate reset a lead's status to
    'researched' but left its existing draft rows in place, so a 'researched'
    lead can still own a draft on real production data. That draft must not be
    silently dropped or trigger the orphan-draft abort: the outreach
    backfill's `OR EXISTS (SELECT 1 FROM drafts ...)` clause pulls this lead in
    regardless of its own status, and since 'researched' is not a valid
    outreach.status, it maps to 'drafted' — the draft genuinely exists and
    belongs in the review queue."""
    # ResearchCo never reached 'drafted' by its own `leads.status`, but a draft
    # hangs off it anyway — exactly the regenerate-then-never-redrafted case.
    await legacy_session.execute(
        text("""
        INSERT INTO drafts (lead_id, subject_line, body)
        VALUES ('22222222-2222-2222-2222-222222222222', 'Hi ResearchCo', 'precious body')
        """)
    )
    await legacy_session.commit()

    await _run_migration(legacy_session)  # must NOT raise / abort

    row = (
        await legacy_session.execute(
            text("""
            SELECT o.status, d.body
            FROM outreach o
            JOIN companies c ON c.id = o.company_id
            JOIN drafts d   ON d.outreach_id = o.id
            WHERE c.company_name = 'ResearchCo'
            """)
        )
    ).one()
    assert row.status == "drafted"
    assert row.body == "precious body"


@pytest_asyncio.fixture
async def legacy_fixture_without_dead_letter(legacy_fixture, legacy_session):
    """A database that never had 004 applied — or that was provisioned by
    create_all before the DeadLetter model existed."""
    await legacy_session.execute(text("DROP TABLE dead_letter"))
    await legacy_session.commit()


@pytest.mark.asyncio
async def test_aborts_when_dead_letter_is_missing(
    legacy_session, legacy_fixture_without_dead_letter
):
    """Dying halfway through `ALTER TABLE dead_letter` against production is far
    worse than refusing to start and naming the file to apply."""
    with pytest.raises(asyncpg.exceptions.RaiseError, match="004_dead_letter.sql"):
        await _run_migration(legacy_session)


@pytest.mark.asyncio
async def test_refuses_to_run_twice(legacy_fixture, legacy_session):
    """A second run finds `leads` already renamed, and stops there rather than
    failing partway through the table surgery."""
    await _run_migration(legacy_session)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="nothing to migrate"):
        await _run_migration(legacy_session)


@pytest.mark.asyncio
async def test_refuses_a_schema_built_from_the_post_split_models(legacy_fixture, legacy_session):
    """A create_all boot on the post-1b models leaves `leads` in place but
    `research` already keyed by company, and renaming a column that is not there
    would abort mid-transaction with a raw Postgres error."""
    await legacy_session.execute(text("DROP VIEW pending_drafts"))
    await legacy_session.execute(text("ALTER TABLE research DROP COLUMN lead_id"))
    await legacy_session.commit()

    with pytest.raises(asyncpg.exceptions.RaiseError, match="already migrated"):
        await _run_migration(legacy_session)


@pytest.mark.asyncio
async def test_refuses_a_dead_letter_table_built_from_the_post_split_models(
    legacy_fixture, legacy_session
):
    """The presence-only preflight check (to_regclass('dead_letter') IS NOT
    NULL) is not enough: a dead_letter table built by create_all from the NEW
    (post-1b) ORM model already has company_id/outreach_id and no lead_id at
    all. Without a shape check it would sail past preflight and only die deep
    in the backfill on a raw 'column dead_letter.lead_id does not exist'."""
    await legacy_session.execute(text("ALTER TABLE dead_letter DROP COLUMN lead_id"))
    await legacy_session.commit()

    with pytest.raises(asyncpg.exceptions.RaiseError, match="already migrated"):
        await _run_migration(legacy_session)


@pytest.mark.asyncio
async def test_adopts_tables_a_create_all_boot_already_made(legacy_fixture, legacy_session):
    """scripts/start.sh runs create_all on every boot, so the new tables can
    already exist (empty) by the time this migration runs. It must adopt them,
    not collide with them."""
    await legacy_session.execute(
        text("""
        CREATE TABLE companies (
            id UUID PRIMARY KEY, company_name TEXT NOT NULL, company_url TEXT,
            linkedin_url TEXT, founder_name TEXT, funding_stage TEXT, headcount INT,
            industry TEXT, research_status TEXT NOT NULL, error_msg TEXT,
            created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
        )
        """)
    )
    await legacy_session.commit()

    await _run_migration(legacy_session)

    assert (await legacy_session.execute(text("SELECT COUNT(*) FROM companies"))).scalar_one() == 9


@pytest.mark.asyncio
async def test_create_all_and_the_migration_agree_on_indexes_and_constraints(
    legacy_fixture, legacy_session, legacy_engine
):
    """The two provisioning paths must converge.

    Production provisions with Base.metadata.create_all (scripts/start.sh), not
    with these files, so an index that exists only in the SQL — the partial
    company_contacts_eligible_idx, say — would simply be absent from the
    database the selection queries actually run against.
    """
    await _run_migration(legacy_session)
    from_migration = await _schema_fingerprint(legacy_engine)
    await legacy_session.close()

    await _reset_schema(legacy_engine)
    async with legacy_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from_create_all = await _schema_fingerprint(legacy_engine)

    assert from_create_all["indexes"] == from_migration["indexes"]
    assert from_create_all["constraints"] == from_migration["constraints"]
