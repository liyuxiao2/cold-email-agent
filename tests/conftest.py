import pathlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cold_email.auth.session import SESSION_COOKIE, mint_session
from cold_email.config import settings
from cold_email.database import ROLE_ADMIN, ROLE_USER, Base, User, get_async_session

# rsplit on the final "/" rather than a plain string replace: the latter also
# matches "cold_email" inside the username (postgresql://cold_email:...@...),
# producing a URL that authenticates as a nonexistent "cold_email_test" role.
TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + "/cold_email_test"

VIEWS_SQL_PATH = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "views.sql"
_VIEW_NAMES = ("pending_drafts", "pending_sends", "available_contacts")


@pytest_asyncio.fixture(scope="function")
async def async_session() -> AsyncSession:
    """
    Creates all tables fresh for each test, yields a session, then drops everything.
    Requires: docker compose up -d and cold_email_test database to exist.
    Create the test DB once with:
        psql postgresql://cold_email:secret@localhost:5432 -c "CREATE DATABASE cold_email_test;"
    """
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(async_session):
    """The admin who owns outreach rows in model-level tests."""
    user = User(email="admin@example.com", google_sub="sub-admin", role=ROLE_ADMIN)
    async_session.add(user)
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def admin_user_id(admin_user):
    """Just the id, for the many existing tests that only need that."""
    return admin_user.id


def _app():
    """Import the FastAPI app lazily.

    A module-level import would break collection for the entire suite, not just
    the API tests, while the routes still reference models this stack is in the
    middle of replacing.
    """
    from cold_email.api.main import app

    return app


@pytest_asyncio.fixture
async def client(async_session):
    """Unauthenticated API client backed by the test database."""
    app = _app()

    async def _override():
        yield async_session

    app.dependency_overrides[get_async_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _client_for_role(async_session, role: str, email: str):
    app = _app()
    user = User(email=email, google_sub=f"sub-{role}", role=role)
    async_session.add(user)
    await async_session.commit()

    async def _override():
        yield async_session

    app.dependency_overrides[get_async_session] = _override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: mint_session(user.id)},
    ), user


@pytest_asyncio.fixture
async def user_client(async_session):
    """Client carrying a real session cookie for a role='user' account.

    A real cookie rather than a monkeypatched dependency, so gating tests
    exercise the actual verify_session -> DB lookup -> role check chain.
    """
    c, _ = await _client_for_role(async_session, ROLE_USER, "user@example.com")
    async with c:
        yield c
    _app().dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(async_session):
    """Client carrying a real session cookie for a role='admin' account."""
    c, _ = await _client_for_role(async_session, ROLE_ADMIN, "admin@example.com")
    async with c:
        yield c
    _app().dependency_overrides.clear()


@pytest.fixture
def sync_session_for(monkeypatch, async_session):
    """Point the workers' sync session factory at the same physical test DB.

    Workers call get_sync_session(), a contextmanager yielding a plain
    sqlalchemy.orm.Session over the sync engine; tests drive async_session, an
    AsyncSession over an async engine. A worker helper invoked directly from a
    test needs its writes visible to that same test's assertions.

    R18 / the brief's original shim: the brief proposed wrapping
    `async_session.sync_session` in a `__getattr__` proxy so the worker and the
    test would literally share one transaction. That does not work — verified
    by running it: `async_session.sync_session` is the sync-style facade
    AsyncSession delegates to *internally*, and its IO must run inside
    SQLAlchemy's greenlet context. Calling `session.get()` / `session.commit()`
    on it directly from ordinary sync code (which is what a Celery worker
    function does) raises `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has
    not been called`, because there is no greenlet bridging that call back into
    the async event loop the way `await conn.run_sync(...)` would.

    Working replacement: a genuine sync Session on its own connection to the
    SAME physical Postgres test database (`cold_email_test`), rather than the
    same transaction. This works because:
      1. async_session's setup writes are committed with `await
         async_session.commit()` before a worker helper is ever called, so
         they are durably visible to any other connection.
      2. The worker helper does its own `session.commit()` inside
         get_sync_session()'s `with` block, on this separate sync session.
      3. Postgres's default READ COMMITTED isolation gives every new statement
         a fresh snapshot (unlike REPEATABLE READ, which snapshots once per
         transaction) — so `await async_session.refresh(obj)` or a fresh
         `select()` on async_session, run *after* the worker call returns,
         sees the sync session's just-committed row.
      4. Cleanup is automatic: async_session's fixture teardown drops all
         tables after the test; this fixture disposes its own engine/session
         so it leaves no dangling connection behind.

    Later tasks: call this fixture, then write via async_session as usual,
    call the worker function (which writes through the now-patched
    get_sync_session), and read the result back via async_session
    (await async_session.refresh(obj) or a fresh select()) — in that order,
    so the read happens after the sync side's commit.
    """
    import contextlib

    import sqlalchemy
    from sqlalchemy.orm import Session

    sync_engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    session = Session(bind=sync_engine, expire_on_commit=False)

    @contextlib.contextmanager
    def _get_sync_session():
        yield session

    for module in (
        "cold_email.workers.shared.db_helpers",
        "cold_email.workers.research.helpers.db_helpers",
        "cold_email.workers.drafting.helpers.db_helpers",
        "cold_email.workers.logistics.helpers.db_helpers",
        # discovery.py, and drafting.py's load_sender_context /
        # drafting_recovery_task, call get_sync_session() directly rather
        # than through a db_helpers submodule, so they need patching here too.
        "cold_email.workers.discovery.discovery",
        "cold_email.workers.drafting.drafting",
    ):
        try:
            monkeypatch.setattr(f"{module}.get_sync_session", _get_sync_session, raising=False)
        except ImportError:
            # Some worker modules still import the deleted Lead model and are
            # owned by later tasks in this stack (see task-4/5 report). Their
            # import error is expected right now; once a later task fixes the
            # module, this loop starts patching it with no further changes here.
            continue

    yield session

    session.close()
    sync_engine.dispose()


@pytest_asyncio.fixture
async def pending_views(async_session):
    """Apply pending_drafts / pending_sends / available_contacts to the test DB.

    async_session's create_all does not create database VIEWS at all (see
    tests/test_views.py's docstring) — a test that drives a worker helper
    which reads one of these views via raw SQL (e.g. fetch_pending_drafts,
    fetch_send_inputs) needs them provisioned first, same as scripts/start.sh
    does in production via scripts/apply_views.py.

    Torn down before the test ends (not left for async_session's own
    teardown): Postgres refuses DROP TABLE while a view still depends on it.

    Takes `async_session` so it can roll it back before dropping the views: a
    test that reads a view directly through async_session (e.g.
    contact_selection.select_contact) leaves that session idle-in-transaction
    holding an AccessShareLock on the view, which would otherwise deadlock the
    DROP VIEW below waiting for an AccessExclusiveLock forever.
    """
    import sqlalchemy

    engine = sqlalchemy.create_engine(TEST_DB_URL.replace("+asyncpg", "+psycopg2"))
    with engine.connect() as conn:
        conn.exec_driver_sql(VIEWS_SQL_PATH.read_text())
        conn.commit()

    yield

    await async_session.rollback()

    with engine.connect() as conn:
        for view in _VIEW_NAMES:
            conn.exec_driver_sql(f"DROP VIEW IF EXISTS {view} CASCADE")
        conn.commit()
    engine.dispose()


@pytest_asyncio.fixture
async def other_user_outreach(async_session):
    """A drafted outreach row owned by somebody other than `user_client`."""
    from cold_email.database import OUTREACH_DRAFTED, ROLE_USER, Company, Outreach, User

    other = User(email="other@example.com", google_sub="sub-other", role=ROLE_USER)
    company = Company(company_name="OtherCo")
    async_session.add_all([other, company])
    await async_session.commit()

    outreach = Outreach(user_id=other.id, company_id=company.id, status=OUTREACH_DRAFTED)
    async_session.add(outreach)
    await async_session.commit()
    return outreach


@pytest_asyncio.fixture
async def seeded_profile(async_session, user_client):
    """A complete profile with a résumé, owned by user_client's account."""
    from sqlalchemy import select

    from cold_email.database import Profile, User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()
    profile = Profile(
        user_id=user.id,
        name="Test User",
        intro="I am a test.",
        experience_pool=["Acme: a thing"],
        resume_pdf=b"%PDF-1.7\n" + b"x" * 2048,
        resume_filename="cv.pdf",
    )
    async_session.add(profile)
    await async_session.commit()
    return profile


@pytest_asyncio.fixture
async def other_users_profile(async_session):
    """A profile with a DIFFERENT résumé, owned by somebody else."""
    from cold_email.database import ROLE_USER, Profile, User

    other = User(email="other2@example.com", google_sub="sub-other2", role=ROLE_USER)
    async_session.add(other)
    await async_session.commit()

    profile = Profile(
        user_id=other.id,
        name="Other",
        intro="Not you.",
        resume_pdf=b"%PDF-1.7 SOMEONE ELSE",
        resume_filename="other.pdf",
    )
    async_session.add(profile)
    await async_session.commit()
    return profile


@pytest_asyncio.fixture
async def admin_profile(async_session, admin_user_id):
    """A complete profile with a résumé, owned by the admin.

    drafting_task(user_id) drafts exactly one user's rows per call, so worker
    tests that want a full happy-path sweep pass admin_user_id and act on the
    admin's own profile row.
    """
    from cold_email.database import Profile

    profile = Profile(
        user_id=admin_user_id,
        name="Admin User",
        intro="I am the admin.",
        experience_pool=["Acme: a thing"],
        resume_pdf=b"%PDF-1.7\n" + b"x" * 2048,
        resume_filename="cv.pdf",
    )
    async_session.add(profile)
    await async_session.commit()
    return profile


@pytest_asyncio.fixture
async def admin_profile_no_pdf(async_session, admin_user_id):
    """A complete profile with no résumé bytes — effective_resume_text falls
    back to intro + experience_pool, so drafting must still succeed."""
    from cold_email.database import Profile

    profile = Profile(user_id=admin_user_id, name="Admin User", intro="I am the admin.")
    async_session.add(profile)
    await async_session.commit()
    return profile


@pytest_asyncio.fixture
async def admin_gmail_connected(async_session, admin_user_id):
    """Give the admin a usable (encrypted) Gmail refresh token."""
    from cold_email.auth.crypto import encrypt
    from cold_email.database import User

    user = await async_session.get(User, admin_user_id)
    user.gmail_refresh_token_enc = encrypt("rt-admin")
    user.gmail_sender_email = "admin@example.com"
    await async_session.commit()
    return user


async def _add_queued_outreach(async_session, admin_user_id, company_name: str, email: str):
    """Insert one researched company + eligible contact + research row + a
    'queued' outreach row for the admin, ready for a drafting sweep."""
    from cold_email.database import (
        OUTREACH_QUEUED,
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        Outreach,
        Research,
    )

    company = Company(company_name=company_name, research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()

    contact = CompanyContact(
        company_id=company.id, email=email, first_name="Ada", eligible=True, confidence=90
    )
    async_session.add(contact)
    async_session.add(
        Research(
            company_id=company.id,
            tech_stack=["python"],
            recent_news="news",
            hook="hook",
            raw_content="raw",
        )
    )
    await async_session.commit()

    outreach = Outreach(
        user_id=admin_user_id, company_id=company.id, contact_id=contact.id, status=OUTREACH_QUEUED
    )
    async_session.add(outreach)
    await async_session.commit()
    return outreach


@pytest_asyncio.fixture
async def queued_outreach(async_session, admin_user_id, pending_views):
    """One queued outreach row for the admin with an eligible contact and a
    research row — the minimal input a drafting sweep needs."""
    return await _add_queued_outreach(async_session, admin_user_id, "Acme", "ada@acme.com")


@pytest_asyncio.fixture
async def three_queued_outreach(async_session, admin_user_id, pending_views):
    """The same as queued_outreach, but three companies — for asserting a
    per-sweep (not per-lead) résumé read."""
    return [
        await _add_queued_outreach(async_session, admin_user_id, f"Acme{i}", f"ada{i}@acme.com")
        for i in range(3)
    ]


@pytest_asyncio.fixture
async def two_users_queued(async_session, pending_views):
    """Two distinct users, each with exactly one queued outreach row, for
    asserting that a per-user drafting_task sweep touches only its own user's
    row."""
    from cold_email.database import ROLE_USER, User

    user_a = User(email="usera-sweep@example.com", google_sub="sub-usera-sweep", role=ROLE_USER)
    user_b = User(email="userb-sweep@example.com", google_sub="sub-userb-sweep", role=ROLE_USER)
    async_session.add_all([user_a, user_b])
    await async_session.commit()

    await _add_queued_outreach(async_session, user_a.id, "AcmeSweep", "a-contact@acme.com")
    await _add_queued_outreach(async_session, user_b.id, "GlobexSweep", "b-contact@globex.com")

    return {"user_a": user_a.id, "user_b": user_b.id}


@pytest_asyncio.fixture
async def profiles_for_both(async_session, two_users_queued):
    """A complete profile and connected Gmail for both users in
    two_users_queued, so a sweep for either one clears preflight."""
    from cold_email.auth.crypto import encrypt
    from cold_email.database import Profile, User

    for key in ("user_a", "user_b"):
        user_id = two_users_queued[key]
        user = await async_session.get(User, user_id)
        user.gmail_refresh_token_enc = encrypt(f"rt-{key}")
        user.gmail_sender_email = f"{key}@example.com"
        async_session.add(
            Profile(
                user_id=user_id,
                name=key,
                intro=f"I am {key}.",
                experience_pool=["Acme: a thing"],
            )
        )
    await async_session.commit()


@pytest_asyncio.fixture
async def stale_and_fresh_queued(async_session, pending_views):
    """One user's queued row is old enough to look like a lost dispatch; the
    other's was just created. The recovery sweep must pick up only the
    former."""
    from datetime import UTC, datetime, timedelta

    from cold_email.database import ROLE_USER, User

    stale_user = User(email="stale@example.com", google_sub="sub-stale", role=ROLE_USER)
    fresh_user = User(email="fresh@example.com", google_sub="sub-fresh", role=ROLE_USER)
    async_session.add_all([stale_user, fresh_user])
    await async_session.commit()

    stale_outreach = await _add_queued_outreach(
        async_session, stale_user.id, "StaleCo", "stale@stale.co"
    )
    stale_outreach.created_at = datetime.now(UTC) - timedelta(minutes=45)
    await _add_queued_outreach(async_session, fresh_user.id, "FreshCo", "fresh@fresh.co")
    await async_session.commit()

    return {"stale_user": stale_user.id, "fresh_user": fresh_user.id}


@pytest_asyncio.fixture
async def byok_admin(async_session, admin_user_id):
    """Give the admin a BYOK LLM key, so resolve_llm_credentials returns
    is_byok=True for their sweeps."""
    from cold_email.auth.crypto import encrypt
    from cold_email.database import User

    user = await async_session.get(User, admin_user_id)
    user.llm_api_key_enc = encrypt("byok-secret-key")
    user.llm_provider = "groq"
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def extra_users(async_session):
    """Five additional users, for testing the global per-contact cap."""
    from cold_email.database import ROLE_USER, User

    users = [
        User(email=f"u{i}@example.com", google_sub=f"sub-u{i}", role=ROLE_USER) for i in range(5)
    ]
    async_session.add_all(users)
    await async_session.commit()
    return [u.id for u in users]


@pytest_asyncio.fixture
async def company_factory(async_session):
    """A factory for a distinct `researched` company per call.

    UNIQUE(user_id, company_id) on outreach forbids two rows for the same
    user reusing one company, so quota tests that create several outreach
    rows for one user need a fresh company each time.
    """
    import itertools

    from cold_email.database import RESEARCH_RESEARCHED, Company

    counter = itertools.count()

    async def _factory():
        company = Company(
            company_name=f"QuotaCo{next(counter)}", research_status=RESEARCH_RESEARCHED
        )
        async_session.add(company)
        await async_session.commit()
        return company

    return _factory


@pytest_asyncio.fixture
async def pool_fixture(async_session, pending_views):
    """The company pool's baseline scenario for /api/companies tests.

    ResearchedCo is the only company that should ever appear in a plain,
    unfiltered listing: FoundCo and FailedCo have not finished research, and
    GenericOnlyCo has no ELIGIBLE contact (its one contact is a generic
    inbox), so it is excluded by the pool's availability check the same way
    an exhausted company is.
    """
    from cold_email.database import (
        RESEARCH_FAILED,
        RESEARCH_FOUND,
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        Research,
    )

    researched = Company(
        company_name="ResearchedCo",
        research_status=RESEARCH_RESEARCHED,
        industry="Fintech",
        headcount=10,
    )
    found = Company(company_name="FoundCo", research_status=RESEARCH_FOUND)
    failed = Company(company_name="FailedCo", research_status=RESEARCH_FAILED)
    generic_only = Company(company_name="GenericOnlyCo", research_status=RESEARCH_RESEARCHED)
    async_session.add_all([researched, found, failed, generic_only])
    await async_session.commit()

    async_session.add_all(
        [
            CompanyContact(
                company_id=researched.id,
                email="founder@researched.co",
                first_name="Fay",
                position="Founder",
                is_founder=True,
                eligible=True,
                confidence=90,
            ),
            CompanyContact(
                company_id=researched.id,
                email="cto@researched.co",
                first_name="Cody",
                position="CTO",
                is_founder=False,
                eligible=True,
                confidence=80,
            ),
            CompanyContact(
                company_id=generic_only.id,
                email="info@genericonly.co",
                first_name="Gen",
                position="Info",
                is_founder=False,
                eligible=False,
                confidence=10,
            ),
            Research(
                company_id=researched.id,
                hook="A great hook",
                tech_stack=["python"],
                recent_news="news",
                raw_content="raw",
            ),
        ]
    )
    await async_session.commit()
    return {
        "researched": researched,
        "found": found,
        "failed": failed,
        "generic_only": generic_only,
    }


@pytest_asyncio.fixture
async def exhausted_company(async_session, pending_views):
    """ExhaustedCo has one eligible contact already used by settings.contact_cap
    different users, so it must drop out of everyone's pool."""
    from cold_email.config import settings
    from cold_email.database import (
        RESEARCH_RESEARCHED,
        ROLE_USER,
        Company,
        CompanyContact,
        Outreach,
        User,
    )

    company = Company(company_name="ExhaustedCo", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()

    contact = CompanyContact(
        company_id=company.id,
        email="only@exhausted.co",
        first_name="Ex",
        eligible=True,
        confidence=90,
    )
    async_session.add(contact)
    await async_session.commit()

    users = [
        User(email=f"exhaust{i}@example.com", google_sub=f"sub-exhaust{i}", role=ROLE_USER)
        for i in range(settings.contact_cap)
    ]
    async_session.add_all(users)
    await async_session.commit()

    async_session.add_all(
        [Outreach(user_id=u.id, company_id=company.id, contact_id=contact.id) for u in users]
    )
    await async_session.commit()
    return company


@pytest_asyncio.fixture
async def targeted_by_user_company(async_session, user_client, pending_views):
    """TargetedCo has an eligible contact and an existing outreach row owned by
    `user_client`'s own account — it must be hidden from that user but stay
    visible to everyone else."""
    from sqlalchemy import select

    from cold_email.database import RESEARCH_RESEARCHED, Company, CompanyContact, Outreach, User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()

    company = Company(company_name="TargetedCo", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()

    contact = CompanyContact(
        company_id=company.id,
        email="t@targetedco.co",
        first_name="Tay",
        eligible=True,
        confidence=90,
    )
    async_session.add(contact)
    await async_session.commit()

    outreach = Outreach(user_id=user.id, company_id=company.id, contact_id=contact.id)
    async_session.add(outreach)
    await async_session.commit()
    return company


@pytest_asyncio.fixture
async def set_quota(async_session, user_client):
    """Set `monthly_draft_quota` on `user_client`'s own account."""
    from sqlalchemy import select

    from cold_email.database import User

    async def _set(n: int):
        user = (
            await async_session.execute(select(User).where(User.email == "user@example.com"))
        ).scalar_one()
        user.monthly_draft_quota = n
        await async_session.commit()
        return user

    return _set


@pytest.fixture
def captured_drafts(monkeypatch):
    """Record create_draft's kwargs (and the creds it was called with) instead
    of hitting the real Gmail API."""
    calls = []

    def fake_create_draft(creds, **kwargs):
        calls.append({"creds": creds, **kwargs})
        return "gmail-fake-id"

    monkeypatch.setattr("cold_email.workers.drafting.drafting.create_draft", fake_create_draft)
    return calls


@pytest_asyncio.fixture
async def approved_outreach_factory(async_session, admin_user_id, pending_views):
    """A factory creating one 'approved' outreach row per call: a distinct
    company (UNIQUE(user_id, company_id) forbids reusing one company for the
    same user) with an eligible contact, a draft carrying a gmail_draft_id,
    and Gmail credentials on the admin (the owner) -- the minimal input the
    due-send scanner and logistics_task need.

    Starts 'approved', not 'sending': the scanner tests exercise
    claim_due_sends flipping it to 'sending' themselves.
    """
    import itertools

    from cold_email.auth.crypto import encrypt
    from cold_email.database import (
        OUTREACH_APPROVED,
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        Draft,
        Outreach,
        User,
    )

    counter = itertools.count()

    user = await async_session.get(User, admin_user_id)
    if not user.gmail_refresh_token_enc:
        user.gmail_refresh_token_enc = encrypt("rt-admin")
        user.gmail_sender_email = "admin@example.com"
        await async_session.commit()

    async def _factory(scheduled_send_at=None):
        n = next(counter)
        company = Company(company_name=f"SendCo{n}", research_status=RESEARCH_RESEARCHED)
        async_session.add(company)
        await async_session.commit()

        contact = CompanyContact(
            company_id=company.id, email=f"contact{n}@sendco.com", eligible=True, confidence=90
        )
        async_session.add(contact)
        await async_session.commit()

        outreach = Outreach(
            user_id=admin_user_id,
            company_id=company.id,
            contact_id=contact.id,
            status=OUTREACH_APPROVED,
            scheduled_send_at=scheduled_send_at,
        )
        async_session.add(outreach)
        await async_session.commit()

        async_session.add(
            Draft(
                outreach_id=outreach.id,
                subject_line=f"Hi SendCo{n}",
                body="body",
                gmail_draft_id=f"gd-{n}",
            )
        )
        await async_session.commit()
        return outreach

    return _factory


@pytest_asyncio.fixture
async def two_users_approved(async_session, pending_views):
    """Two distinct users, each owning one outreach row already claimed
    'sending' (draft + Gmail credentials included), for asserting that
    logistics_task always sends from the OUTREACH ROW'S OWNER's mailbox --
    never a global sender or whoever happens to be calling.

    Pre-claimed 'sending' (not 'approved'): the only consumer of this fixture
    calls logistics_task directly, bypassing send_due_task's own claim.
    """
    from cold_email.auth.crypto import encrypt
    from cold_email.database import (
        OUTREACH_SENDING,
        RESEARCH_RESEARCHED,
        ROLE_USER,
        Company,
        CompanyContact,
        Draft,
        Outreach,
        User,
    )

    user_a = User(email="a@example.com", google_sub="sub-a-send", role=ROLE_USER)
    user_b = User(email="b@example.com", google_sub="sub-b-send", role=ROLE_USER)
    user_a.gmail_refresh_token_enc = encrypt("rt-a")
    user_a.gmail_sender_email = "a@example.com"
    user_b.gmail_refresh_token_enc = encrypt("rt-b")
    user_b.gmail_sender_email = "b@example.com"
    async_session.add_all([user_a, user_b])
    await async_session.commit()

    async def _make(user, name, email):
        company = Company(company_name=name, research_status=RESEARCH_RESEARCHED)
        async_session.add(company)
        await async_session.commit()
        contact = CompanyContact(company_id=company.id, email=email, eligible=True, confidence=90)
        async_session.add(contact)
        await async_session.commit()
        outreach = Outreach(
            user_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            status=OUTREACH_SENDING,
        )
        async_session.add(outreach)
        await async_session.commit()
        async_session.add(
            Draft(
                outreach_id=outreach.id,
                subject_line=f"Hi {name}",
                body="body",
                gmail_draft_id=f"gd-{name}",
            )
        )
        await async_session.commit()
        return outreach

    outreach_a = await _make(user_a, "CoA", "contact-a@co.com")
    outreach_b = await _make(user_b, "CoB", "contact-b@co.com")

    return {"outreach_a": outreach_a, "outreach_b": outreach_b}


@pytest_asyncio.fixture
async def with_cadence(async_session, user_client):
    """Set a daily cadence (see tests/test_scheduling_api.py's CADENCE) on
    user_client's own account, so approve's empty-body path has a slot to
    compute instead of falling through to NULL."""
    from sqlalchemy import select

    from cold_email.database import User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()
    user.send_cadence = {
        "max_per_day": 2,
        "days": [0, 1, 2, 3, 4, 5, 6],
        "window_start": "09:00",
        "window_end": "17:00",
        "timezone": "America/Toronto",
    }
    await async_session.commit()
    return user
