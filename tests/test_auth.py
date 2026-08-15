import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cold_email.api.routes.auth import upsert_user
from cold_email.auth.crypto import decrypt
from cold_email.auth.google_oauth import GoogleIdentity
from cold_email.auth.signup_policy import is_signup_allowed
from cold_email.config import settings
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
async def test_upsert_does_not_rebind_an_already_bound_row_by_email(async_session):
    """A row already bound to sub 'A' must not be silently reassigned to sub
    'B' just because a new sign-in shares its email — that would be an
    account takeover (including of the admin row and its stored, decryptable
    refresh token). The email fallback must only ever claim a row whose
    google_sub is still NULL.

    `email` is unique in the schema, so once the takeover path is closed,
    this now-unmatched identity can't be inserted as a fresh row either — it
    surfaces as a loud IntegrityError instead of a silent takeover, which is
    the safe outcome: no ambiguity about which account just got a new owner.
    """
    bound = User(email="person@example.com", google_sub="google-sub-A", role=ROLE_USER)
    async_session.add(bound)
    await async_session.commit()

    with pytest.raises(IntegrityError):
        await upsert_user(async_session, _identity(sub="google-sub-B"))
    await async_session.rollback()

    rows = (await async_session.execute(select(User))).scalars().all()
    assert len(rows) == 1
    assert rows[0].google_sub == "google-sub-A"  # original row untouched


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

    # Not testing the allowlist here — just the happy path — so allow this
    # identity's email explicitly rather than depending on whatever
    # ADMIN_EMAIL happens to be set to in this environment.
    monkeypatch.setattr(settings, "allowed_signup_emails", ["person@example.com"])
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


# --- Signup allowlist (default-deny) -----------------------------------------


@pytest.fixture
def _clear_allowlist(monkeypatch):
    """Every allowlist test starts from a clean, deliberately-narrow slate."""
    monkeypatch.setattr(settings, "admin_email", "")
    monkeypatch.setattr(settings, "allowed_signup_emails", [])
    monkeypatch.setattr(settings, "allowed_signup_domain", "")


def test_admin_email_is_allowed_even_with_both_allowlists_empty(_clear_allowlist, monkeypatch):
    monkeypatch.setattr(settings, "admin_email", "admin@example.com")
    assert is_signup_allowed("admin@example.com") is True


def test_admin_email_match_is_case_insensitive(_clear_allowlist, monkeypatch):
    monkeypatch.setattr(settings, "admin_email", "Admin@Example.com")
    assert is_signup_allowed("admin@example.com") is True


def test_explicitly_allowed_email_is_allowed(_clear_allowlist, monkeypatch):
    monkeypatch.setattr(settings, "allowed_signup_emails", ["ally@example.com"])
    assert is_signup_allowed("Ally@Example.com") is True


def test_matching_domain_is_allowed(_clear_allowlist, monkeypatch):
    monkeypatch.setattr(settings, "allowed_signup_domain", "example.com")
    assert is_signup_allowed("anyone@example.com") is True
    assert is_signup_allowed("Anyone@Example.COM") is True


def test_non_matching_domain_is_denied(_clear_allowlist, monkeypatch):
    monkeypatch.setattr(settings, "allowed_signup_domain", "example.com")
    assert is_signup_allowed("anyone@other.com") is False


def test_unrecognized_email_is_denied_by_default(_clear_allowlist):
    assert is_signup_allowed("stranger@nowhere.com") is False


@pytest.mark.asyncio
async def test_callback_with_allowed_email_creates_a_user(client, monkeypatch, async_session):
    from cold_email.api.routes import auth as auth_routes
    from cold_email.auth.session import mint_state

    monkeypatch.setattr(settings, "admin_email", "")
    monkeypatch.setattr(settings, "allowed_signup_emails", ["person@example.com"])
    monkeypatch.setattr(settings, "allowed_signup_domain", "")
    monkeypatch.setattr(auth_routes, "exchange_code", lambda code: _identity())

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "good-code", "state": mint_state()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == settings.frontend_url
    users = (await async_session.execute(select(User))).scalars().all()
    assert len(users) == 1
    assert users[0].email == "person@example.com"


@pytest.mark.asyncio
async def test_callback_with_denied_email_creates_no_user(client, monkeypatch, async_session):
    from cold_email.api.routes import auth as auth_routes
    from cold_email.auth.session import mint_state

    monkeypatch.setattr(settings, "admin_email", "")
    monkeypatch.setattr(settings, "allowed_signup_emails", [])
    monkeypatch.setattr(settings, "allowed_signup_domain", "")
    monkeypatch.setattr(auth_routes, "exchange_code", lambda code: _identity())

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "good-code", "state": mint_state()},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=not_allowed" in response.headers["location"]
    assert (await async_session.execute(select(User))).scalars().all() == []
