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
async def admin_user_id(async_session):
    """The admin who owns outreach rows in model-level tests."""
    user = User(email="admin@example.com", google_sub="sub-admin", role=ROLE_ADMIN)
    async_session.add(user)
    await async_session.commit()
    return user.id


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
