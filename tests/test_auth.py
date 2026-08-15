import pytest
from sqlalchemy import select

from cold_email.api.routes.auth import upsert_user
from cold_email.auth.crypto import decrypt
from cold_email.auth.google_oauth import GoogleIdentity
from cold_email.database import ROLE_ADMIN, ROLE_USER, User


def _identity(**overrides) -> GoogleIdentity:
    base = {
        "sub": "google-sub-1",
        "email": "person@example.com",
        "name": "A Person",
        "picture_url": "https://example.com/p.jpg",
        "refresh_token": "rt-secret",
    }
    return GoogleIdentity(**{**base, **overrides})


@pytest.mark.asyncio
async def test_upsert_creates_a_user(async_session):
    user = await upsert_user(async_session, _identity())
    assert user.email == "person@example.com"
    assert user.google_sub == "google-sub-1"
    assert user.role == ROLE_USER


@pytest.mark.asyncio
async def test_refresh_token_is_stored_encrypted(async_session):
    user = await upsert_user(async_session, _identity())
    assert user.gmail_refresh_token_enc is not None
    assert b"rt-secret" not in user.gmail_refresh_token_enc
    assert decrypt(user.gmail_refresh_token_enc) == "rt-secret"


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_google_sub(async_session):
    first = await upsert_user(async_session, _identity())
    second = await upsert_user(async_session, _identity(name="Renamed"))
    assert first.id == second.id
    assert second.name == "Renamed"

    count = len((await async_session.execute(select(User))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_seeded_admin_is_claimed_by_email_and_keeps_its_role(async_session):
    """The admin row is seeded by email with a NULL google_sub. First sign-in
    must fill the sub and preserve role='admin' — silently demoting the only
    admin would lock discovery and research away from everyone."""
    async_session.add(User(email="person@example.com", role=ROLE_ADMIN, google_sub=None))
    await async_session.commit()

    user = await upsert_user(async_session, _identity())
    assert user.role == ROLE_ADMIN
    assert user.google_sub == "google-sub-1"
    assert len((await async_session.execute(select(User))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_upsert_without_refresh_token_leaves_column_null(async_session):
    user = await upsert_user(async_session, _identity(refresh_token=None))
    assert user.gmail_refresh_token_enc is None


@pytest.mark.asyncio
async def test_upsert_preserves_an_existing_refresh_token(async_session):
    """A re-login that returns no refresh token must not wipe a working one."""
    await upsert_user(async_session, _identity())
    user = await upsert_user(async_session, _identity(refresh_token=None))
    assert decrypt(user.gmail_refresh_token_enc) == "rt-secret"


@pytest.mark.asyncio
async def test_login_returns_an_authorize_url_with_a_signed_state(client):
    body = (await client.get("/api/auth/google/login")).json()
    assert "accounts.google.com" in body["authorize_url"]

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(body["authorize_url"]).query)["state"][0]
    from cold_email.auth.session import verify_state

    assert verify_state(state) is True


@pytest.mark.asyncio
async def test_callback_with_tampered_state_creates_no_user(client, async_session):
    response = await client.get(
        "/api/auth/google/callback", params={"code": "c", "state": "forged"}
    )
    assert response.status_code == 400
    assert (await async_session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_callback_sets_an_httponly_session_cookie(client, monkeypatch, async_session):
    from cold_email.api.routes import auth as auth_routes
    from cold_email.auth.session import SESSION_COOKIE, mint_state

    monkeypatch.setattr(auth_routes, "exchange_code", lambda code: _identity())

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "good-code", "state": mint_state()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie


@pytest.mark.asyncio
async def test_callback_redirects_to_login_on_exchange_failure(client, monkeypatch):
    from cold_email.api.routes import auth as auth_routes
    from cold_email.auth.google_oauth import OAuthExchangeFailed
    from cold_email.auth.session import mint_state

    def boom(code):
        raise OAuthExchangeFailed("invalid_grant")

    monkeypatch.setattr(auth_routes, "exchange_code", boom)

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "stale", "state": mint_state()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=oauth_failed" in response.headers["location"]


@pytest.mark.asyncio
async def test_me_reports_gmail_connection_state(async_session, user_client):
    body = (await user_client.get("/api/auth/me")).json()
    assert body["role"] == ROLE_USER
    assert body["gmail_connected"] is False


@pytest.mark.asyncio
async def test_logout_clears_the_cookie(user_client):
    response = await user_client.post("/api/auth/logout")
    assert response.status_code == 200
    assert (
        'ce_session=""' in response.headers["set-cookie"]
        or "Max-Age=0" in response.headers["set-cookie"]
    )
