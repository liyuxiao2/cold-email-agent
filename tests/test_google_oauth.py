from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from cold_email.auth.google_oauth import (
    GOOGLE_SCOPES,
    OAuthExchangeFailed,
    build_authorize_url,
    exchange_code,
)

# A Google id_token payload is a JWT; we only read its claims, so tests build
# an unsigned one and the module is configured not to re-verify the signature
# (the token arrived over TLS directly from Google's token endpoint).
FAKE_ID_TOKEN_CLAIMS = {
    "sub": "1234567890",
    "email": "person@example.com",
    "email_verified": True,
    "name": "A Person",
    "picture": "https://example.com/p.jpg",
}


def _id_token(**claim_overrides) -> str:
    import jwt

    claims = {**FAKE_ID_TOKEN_CLAIMS, **claim_overrides}
    return jwt.encode(claims, "irrelevant", algorithm="HS256")


def test_authorize_url_requests_offline_access_and_forces_consent():
    """Without prompt=consent, Google returns a refresh token only on a user's
    first-ever authorization — so a re-signup silently yields an account that
    cannot send email."""
    params = parse_qs(urlparse(build_authorize_url("state-token")).query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-token"]


def test_authorize_url_requests_all_scopes():
    params = parse_qs(urlparse(build_authorize_url("s")).query)
    requested = params["scope"][0].split()
    for scope in GOOGLE_SCOPES:
        assert scope in requested


def test_scopes_include_identity_and_gmail_compose():
    assert "openid" in GOOGLE_SCOPES
    assert "email" in GOOGLE_SCOPES
    assert "profile" in GOOGLE_SCOPES
    assert "https://www.googleapis.com/auth/gmail.compose" in GOOGLE_SCOPES


def test_exchange_code_returns_identity(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "at",
                "refresh_token": "rt-secret",
                "id_token": _id_token(),
            },
        )

    monkeypatch.setattr(httpx, "post", lambda *a, **k: handler(None))

    identity = exchange_code("auth-code")
    assert identity.sub == "1234567890"
    assert identity.email == "person@example.com"
    assert identity.name == "A Person"
    assert identity.picture_url == "https://example.com/p.jpg"
    assert identity.refresh_token == "rt-secret"  # noqa: S105 (test fixture, not a real credential)


def test_exchange_code_tolerates_missing_refresh_token(monkeypatch):
    """Google omits refresh_token when the user has consented before. That is
    not a login failure — only a send failure — so the identity must survive."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, json={"access_token": "at", "id_token": _id_token()}),
    )
    assert exchange_code("code").refresh_token is None


def test_exchange_code_raises_on_google_error(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    with pytest.raises(OAuthExchangeFailed):
        exchange_code("stale-code")


def test_exchange_code_rejects_unverified_email(monkeypatch):
    """The canonical Sign-in-with-Google pitfall: an id_token can carry an
    unverified email, and this module uses email as an identity key
    (seeded-admin match, signup allowlist)."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={"access_token": "at", "id_token": _id_token(email_verified=False)},
        ),
    )
    with pytest.raises(OAuthExchangeFailed):
        exchange_code("code")


def test_exchange_code_rejects_missing_email_verified_claim(monkeypatch):
    claims = {k: v for k, v in FAKE_ID_TOKEN_CLAIMS.items() if k != "email_verified"}
    import jwt

    token = jwt.encode(claims, "irrelevant", algorithm="HS256")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, json={"access_token": "at", "id_token": token}),
    )
    with pytest.raises(OAuthExchangeFailed):
        exchange_code("code")
