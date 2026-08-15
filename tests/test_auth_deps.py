import uuid

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from cold_email.auth.deps import get_current_user, require_admin
from cold_email.auth.session import SESSION_COOKIE, mint_session
from cold_email.database import ROLE_ADMIN, ROLE_USER, User, get_async_session


def _app(session_factory):
    app = FastAPI()

    @app.get("/me")
    async def me(user: User = Depends(get_current_user)):
        return {"email": user.email}

    @app.get("/admin-only")
    async def admin_only(user: User = Depends(require_admin)):
        return {"email": user.email}

    app.dependency_overrides[get_async_session] = session_factory
    return app


@pytest.fixture
def make_client(async_session):
    async def _factory():
        yield async_session

    app = _app(_factory)

    def build(token: str | None = None):
        cookies = {SESSION_COOKIE: token} if token else {}
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
        )

    return build


@pytest.mark.asyncio
async def test_no_cookie_is_401(make_client):
    async with make_client() as client:
        assert (await client.get("/me")).status_code == 401


@pytest.mark.asyncio
async def test_malformed_cookie_is_401(make_client):
    async with make_client("not-a-jwt") as client:
        assert (await client.get("/me")).status_code == 401


@pytest.mark.asyncio
async def test_valid_session_for_deleted_user_is_401(make_client):
    """A session outliving its user row means logged out, not a 500."""
    async with make_client(mint_session(uuid.uuid4())) as client:
        assert (await client.get("/me")).status_code == 401


@pytest.mark.asyncio
async def test_valid_session_resolves_the_user(async_session, make_client):
    user = User(email="u@example.com", google_sub="s-u", role=ROLE_USER)
    async_session.add(user)
    await async_session.commit()

    async with make_client(mint_session(user.id)) as client:
        response = await client.get("/me")
        assert response.status_code == 200
        assert response.json()["email"] == "u@example.com"


@pytest.mark.asyncio
async def test_non_admin_gets_403_not_401(async_session, make_client):
    """403 not 401: the caller IS authenticated, just not authorized."""
    user = User(email="plain@example.com", google_sub="s-p", role=ROLE_USER)
    async_session.add(user)
    await async_session.commit()

    async with make_client(mint_session(user.id)) as client:
        assert (await client.get("/admin-only")).status_code == 403


@pytest.mark.asyncio
async def test_admin_passes_require_admin(async_session, make_client):
    admin = User(email="admin@example.com", google_sub="s-a", role=ROLE_ADMIN)
    async_session.add(admin)
    await async_session.commit()

    async with make_client(mint_session(admin.id)) as client:
        assert (await client.get("/admin-only")).status_code == 200
