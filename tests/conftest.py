import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cold_email.api.main import app
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
async def client(async_session):
    """Unauthenticated API client backed by the test database."""

    async def _override():
        yield async_session

    app.dependency_overrides[get_async_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _client_for_role(async_session, role: str, email: str):
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
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(async_session):
    """Client carrying a real session cookie for a role='admin' account."""
    c, _ = await _client_for_role(async_session, ROLE_ADMIN, "admin@example.com")
    async with c:
        yield c
    app.dependency_overrides.clear()
