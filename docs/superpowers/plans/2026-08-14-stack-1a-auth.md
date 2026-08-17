# Stack 1a — Authentication & Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google Sign-In, a `users` table with encrypted per-user Gmail refresh tokens, session cookies, and admin/user role gating — without touching the existing data model.

**Architecture:** A new `cold_email/auth/` package with four single-purpose modules (Fernet crypto, Google OAuth HTTP, JWT session, FastAPI dependencies). Routes import only `get_current_user` and `require_admin`. The existing Google Cloud OAuth client is reused for login by adding identity scopes. The `leads` table and the whole pipeline are untouched.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, `cryptography` (Fernet), `pyjwt`, `httpx`, pytest, Next.js 15 / React 19

**Spec:** [`docs/superpowers/specs/2026-08-14-stack-1a-auth-design.md`](../specs/2026-08-14-stack-1a-auth-design.md)

**Branch:** `feat/tenancy-auth` off `main`. Push with `git push -u origin feat/tenancy-auth` and open the PR with `gh pr create --base main` (`gt submit` does not work in this repo).

## Global Constraints

- Google OAuth scopes, exactly: `openid`, `email`, `profile`, `https://www.googleapis.com/auth/gmail.compose`
- Authorize URL must include `access_type=offline` and `prompt=consent`. Without `prompt=consent` Google returns a refresh token only on a user's first-ever authorization.
- `role` values are exactly `'user'` and `'admin'`. `TEXT` column, not a Postgres enum.
- Users are matched on `google_sub`, never on `email`, once `google_sub` is populated. Google account emails can change; `sub` cannot.
- Session JWT: HS256, 7-day expiry, cookie named `ce_session`, `httpOnly`, `SameSite=None`, `Secure` (controlled by `settings.cookie_secure`).
- `cors_origins` must never be `["*"]` — a wildcard origin with `allow_credentials=True` is rejected by browsers.
- `settings.encryption_key` must be validated at import time. Never boot an app that would write unencrypted tokens.
- `GET /api/health` must remain public. Gating it breaks the Cloud Run health check.
- `gmail_client_id` and `gmail_client_secret` stay in `settings` (app-level). Only `gmail_refresh_token` / `gmail_sender_email` are per-user — and they are not removed from settings until Stack 2.
- Never log a decrypted refresh token, a session JWT, or an authorization code.
- Existing tests must keep passing. Run `uv run pytest` before every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `migrations/005_users.sql` | `users` table + `google_sub` index |
| `cold_email/auth/__init__.py` | Package marker; re-exports `get_current_user`, `require_admin` |
| `cold_email/auth/crypto.py` | Fernet encrypt/decrypt. The only place secrets are enciphered. |
| `cold_email/auth/session.py` | JWT mint/verify + signed `state` nonce |
| `cold_email/auth/google_oauth.py` | Authorize URL, code exchange. The only module that talks to Google. |
| `cold_email/auth/deps.py` | `get_current_user` (401), `require_admin` (403) |
| `cold_email/api/routes/auth.py` | `/auth/google/login`, `/auth/google/callback`, `/auth/me`, `/auth/logout` |
| `cold_email/database.py` | `User` model added |
| `cold_email/config.py` | New settings; `cors_origins` narrowed |
| `scripts/start.sh` | Idempotent admin seed |
| `tests/test_crypto.py` | Fernet round-trip |
| `tests/test_session.py` | JWT + state nonce |
| `tests/test_auth.py` | OAuth callback behaviour (Google mocked) |
| `tests/test_auth_gating.py` | 401/403 matrix |
| `tests/conftest.py` | `user_client` / `admin_client` fixtures |
| `frontend/lib/auth.tsx` | `AuthProvider` + `useAuth` |
| `frontend/app/login/page.tsx` | Sign-in page |
| `frontend/components/*.tsx` | `page.tsx` split into ReviewDeck / LeadExplorer / PipelineStats / AdminPanel |

---

### Task 1: Dependencies and configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `cold_email/config.py`
- Modify: `cold_email/api/main.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `settings.session_secret`, `settings.encryption_key`, `settings.google_redirect_uri`, `settings.frontend_url`, `settings.admin_email`, `settings.cookie_secure`, `settings.cors_origins` (narrowed to an explicit list)

- [ ] **Step 1: Add dependencies**

```bash
uv add "pyjwt>=2.9" "cryptography>=43.0" "httpx>=0.27"
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
from cold_email.config import settings


def test_cors_origins_is_not_wildcard():
    """A wildcard origin with allow_credentials=True is rejected by browsers.

    Cookie-based sessions from Vercel to Cloud Run silently fail if this
    regresses, so it is asserted rather than left to review.
    """
    assert "*" not in settings.cors_origins


def test_auth_settings_exist():
    for attr in (
        "session_secret",
        "encryption_key",
        "google_redirect_uri",
        "frontend_url",
        "admin_email",
        "cookie_secure",
    ):
        assert hasattr(settings, attr), f"missing setting: {attr}"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `test_cors_origins_is_not_wildcard` fails (currently `["*"]`), `test_auth_settings_exist` fails on `session_secret`.

- [ ] **Step 4: Add the settings**

In `cold_email/config.py`, inside `class Settings`, replace the existing `cors_origins` line and add the new block:

```python
    # --- Auth (Stack 1a) ---
    session_secret: str = ""       # HS256 signing key for the session JWT
    encryption_key: str = ""       # Fernet key, 44-char urlsafe base64
    google_redirect_uri: str = ""  # must exactly match the Google console entry
    frontend_url: str = "http://localhost:3000"
    admin_email: str = ""          # seeded with role='admin' on boot
    cookie_secure: bool = True     # False only for local http development

    # Explicit list, never ["*"]: a wildcard origin is incompatible with
    # allow_credentials=True, which cookie sessions require.
    cors_origins: list[str] = ["http://localhost:3000"]
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Document the new variables**

Append to `.env.example`:

```bash
# --- Auth (Stack 1a) ---
# Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
SESSION_SECRET=
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# WARNING: losing this key makes every stored Gmail refresh token
# undecryptable and forces every user to re-consent. Back it up.
ENCRYPTION_KEY=
GOOGLE_REDIRECT_URI=http://localhost:8080/api/auth/google/callback
FRONTEND_URL=http://localhost:3000
ADMIN_EMAIL=liyu.xiao@wealthsimple.com
COOKIE_SECURE=false
CORS_ORIGINS=["http://localhost:3000"]
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock cold_email/config.py .env.example tests/test_config.py
git commit -m "feat(auth): add auth settings and narrow cors_origins

A wildcard CORS origin is incompatible with allow_credentials=True, which
cookie-based sessions require. Narrowed to an explicit list and asserted
in tests so it cannot regress."
```

---

### Task 2: Fernet crypto wrapper

**Files:**
- Create: `cold_email/auth/__init__.py`
- Create: `cold_email/auth/crypto.py`
- Test: `tests/test_crypto.py`

**Interfaces:**
- Consumes: `settings.encryption_key`
- Produces: `encrypt(plaintext: str) -> bytes`, `decrypt(token: bytes) -> str`, `EncryptionKeyMissing`

- [ ] **Step 1: Write the failing test**

Create `tests/test_crypto.py`:

```python
import pytest

from cold_email.auth.crypto import decrypt, encrypt


def test_round_trip():
    secret = "1//0eXaMpLeRefreshToken"
    assert decrypt(encrypt(secret)) == secret


def test_ciphertext_is_not_plaintext():
    secret = "1//0eXaMpLeRefreshToken"
    assert secret.encode() not in encrypt(secret)


def test_same_plaintext_gives_different_ciphertexts():
    """Fernet is randomized (it embeds an IV and a timestamp).

    Two encryptions of the same value must differ, or an attacker with read
    access to the table could tell which users share a token.
    """
    secret = "same-value"
    a, b = encrypt(secret), encrypt(secret)
    assert a != b
    assert decrypt(a) == decrypt(b) == secret


def test_decrypt_rejects_tampered_ciphertext():
    from cryptography.fernet import InvalidToken

    token = bytearray(encrypt("value"))
    token[-1] ^= 0xFF
    with pytest.raises(InvalidToken):
        decrypt(bytes(token))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cold_email.auth'`

- [ ] **Step 3: Create the package marker**

Create `cold_email/auth/__init__.py`:

```python
"""Authentication: identity, sessions, and secret encryption.

Four single-purpose modules. Routes import only from `deps`; nothing outside
this package touches Fernet keys or JWT internals.
"""
```

- [ ] **Step 4: Implement the wrapper**

Create `cold_email/auth/crypto.py`:

```python
"""Fernet encryption for secrets at rest (Gmail refresh tokens, LLM API keys).

The single place in the codebase where secrets are enciphered. Fernet is
authenticated encryption (AES-CBC + HMAC), so a tampered ciphertext raises
rather than decrypting to garbage.
"""

from functools import lru_cache

from cryptography.fernet import Fernet

from cold_email.config import settings


class EncryptionKeyMissing(RuntimeError):
    """Raised when ENCRYPTION_KEY is unset or malformed."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Build the Fernet cipher once.

    Fails loudly rather than defaulting: an app that boots without a key would
    silently write unencrypted refresh tokens, and nothing downstream would
    notice until a breach.
    """
    if not settings.encryption_key:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(settings.encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyMissing(f"ENCRYPTION_KEY is malformed: {exc}") from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a secret for storage in a BYTEA column."""
    return _cipher().encrypt(plaintext.encode())


def decrypt(token: bytes) -> str:
    """Decrypt a stored secret. Raises InvalidToken if tampered or wrong key."""
    return _cipher().decrypt(bytes(token)).decode()
```

- [ ] **Step 5: Generate a test key**

Add to `.env` (the local file, not `.env.example`):

```bash
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env
```

- [ ] **Step 6: Run it to verify it passes**

Run: `uv run pytest tests/test_crypto.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add cold_email/auth/__init__.py cold_email/auth/crypto.py tests/test_crypto.py
git commit -m "feat(auth): add Fernet crypto wrapper for secrets at rest"
```

---

### Task 3: Session JWT and signed state nonce

**Files:**
- Create: `cold_email/auth/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `settings.session_secret`
- Produces: `mint_session(user_id: UUID) -> str`, `verify_session(token: str) -> UUID | None`, `mint_state() -> str`, `verify_state(state: str) -> bool`, `SESSION_COOKIE = "ce_session"`, `SESSION_TTL_DAYS = 7`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from cold_email.auth.session import (
    SESSION_COOKIE,
    mint_session,
    mint_state,
    verify_session,
    verify_state,
)
from cold_email.config import settings


def test_session_round_trip():
    user_id = uuid.uuid4()
    assert verify_session(mint_session(user_id)) == user_id


def test_cookie_name():
    assert SESSION_COOKIE == "ce_session"


def test_expired_session_rejected():
    payload = {
        "sub": str(uuid.uuid4()),
        "exp": datetime.now(UTC) - timedelta(seconds=1),
        "typ": "session",
    }
    token = jwt.encode(payload, settings.session_secret, algorithm="HS256")
    assert verify_session(token) is None


def test_session_signed_with_other_secret_rejected():
    payload = {
        "sub": str(uuid.uuid4()),
        "exp": datetime.now(UTC) + timedelta(days=1),
        "typ": "session",
    }
    token = jwt.encode(payload, "an-attackers-secret", algorithm="HS256")
    assert verify_session(token) is None


def test_malformed_session_rejected():
    assert verify_session("not-a-jwt") is None


def test_state_nonce_round_trip():
    assert verify_state(mint_state()) is True


def test_tampered_state_rejected():
    assert verify_state(mint_state() + "x") is False


def test_state_is_not_accepted_as_a_session():
    """A state nonce must not be usable as a session, or anyone who can start
    a login could mint themselves a session for an arbitrary user id."""
    assert verify_session(mint_state()) is None


@pytest.mark.parametrize("bad", ["", "  ", None])
def test_empty_state_rejected(bad):
    assert verify_state(bad) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: cold_email.auth.session`

- [ ] **Step 3: Implement it**

Create `cold_email/auth/session.py`:

```python
"""Stateless session tokens and CSRF state nonces.

A session is an HS256 JWT in an httpOnly cookie: unreadable by JavaScript (so
an XSS cannot exfiltrate it) and verifiable with no database round-trip.

The tradeoff is that an individual token cannot be revoked before it expires.
Acceptable at a 7-day TTL with no billing or destructive admin actions. If
revocation becomes necessary, add a `session_version` integer to `users` and
embed it in the claim.

Both token kinds carry a `typ` claim and are verified against it, so a state
nonce can never be replayed as a session.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from cold_email.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ce_session"
SESSION_TTL_DAYS = 7
STATE_TTL_MINUTES = 10
_ALGORITHM = "HS256"


def mint_session(user_id: uuid.UUID) -> str:
    """Issue a session token for a user."""
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": "session",
            "exp": datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS),
            "iat": datetime.now(UTC),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


def verify_session(token: str | None) -> uuid.UUID | None:
    """Return the user id, or None for any invalid token.

    Returns None rather than raising: every failure mode (expired, tampered,
    malformed, wrong type) means the same thing to the caller — not logged in.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session":
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


def mint_state() -> str:
    """Issue a short-lived signed nonce for the OAuth `state` parameter.

    Signed rather than stored: it needs no server-side session store, and an
    attacker who cannot forge the signature cannot force a victim's browser to
    complete an authorization the attacker began (CSRF on the callback).
    """
    return jwt.encode(
        {
            "typ": "state",
            "jti": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


def verify_state(state: str | None) -> bool:
    """True if `state` is a nonce this server minted and it has not expired."""
    if not state:
        return False
    try:
        payload = jwt.decode(state, settings.session_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return False
    return payload.get("typ") == "state"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_session.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add cold_email/auth/session.py tests/test_session.py
git commit -m "feat(auth): add session JWT and signed OAuth state nonce

Both token kinds carry a typ claim so a state nonce cannot be replayed as
a session token."
```

---

### Task 4: `users` table and migration

**Files:**
- Create: `migrations/005_users.sql`
- Modify: `cold_email/database.py`
- Test: `tests/test_user_model.py`

**Interfaces:**
- Consumes: nothing
- Produces: `database.User` with columns `id`, `google_sub`, `email`, `name`, `picture_url`, `role`, `gmail_refresh_token_enc`, `gmail_sender_email`, `created_at`, `updated_at`; constants `ROLE_USER = "user"`, `ROLE_ADMIN = "admin"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_user_model.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cold_email.database import ROLE_ADMIN, ROLE_USER, User


@pytest.mark.asyncio
async def test_defaults_to_user_role(async_session):
    user = User(email="a@example.com", google_sub="sub-a")
    async_session.add(user)
    await async_session.commit()
    assert user.role == ROLE_USER


@pytest.mark.asyncio
async def test_email_is_unique(async_session):
    async_session.add(User(email="dup@example.com", google_sub="sub-1"))
    await async_session.commit()
    async_session.add(User(email="dup@example.com", google_sub="sub-2"))
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_google_sub_may_be_null_for_a_seeded_admin(async_session):
    """The admin row is seeded by email before that person ever signs in, so
    google_sub must be nullable and filled on first login."""
    admin = User(email="admin@example.com", role=ROLE_ADMIN, google_sub=None)
    async_session.add(admin)
    await async_session.commit()

    found = (
        await async_session.execute(select(User).where(User.email == "admin@example.com"))
    ).scalar_one()
    assert found.google_sub is None
    assert found.role == ROLE_ADMIN


@pytest.mark.asyncio
async def test_refresh_token_column_stores_bytes(async_session):
    user = User(email="b@example.com", google_sub="sub-b", gmail_refresh_token_enc=b"ciphertext")
    async_session.add(user)
    await async_session.commit()
    assert user.gmail_refresh_token_enc == b"ciphertext"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_user_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'User' from 'cold_email.database'`

- [ ] **Step 3: Write the migration**

Create `migrations/005_users.sql`:

```sql
-- 005_users.sql
--
-- The users table: one row per authenticated person. Introduced by Stack 1a
-- (multi-tenant revamp). The data model split (companies / company_contacts /
-- outreach) is Stack 1b; nothing here references leads.
--
-- google_sub is nullable so an admin row can be seeded by email before that
-- person's first sign-in; the OAuth callback fills it in and matches on it
-- thereafter. Matching on google_sub rather than email is deliberate — Google
-- account emails can change, but the subject id cannot.
--
-- gmail_refresh_token_enc holds Fernet ciphertext, never a plaintext token.

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub              TEXT UNIQUE,
    email                   TEXT UNIQUE NOT NULL,
    name                    TEXT,
    picture_url             TEXT,
    role                    TEXT NOT NULL DEFAULT 'user',   -- user | admin
    gmail_refresh_token_enc BYTEA,
    gmail_sender_email      TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_google_sub_idx ON users (google_sub);
```

- [ ] **Step 4: Add the ORM model**

In `cold_email/database.py`, add `LargeBinary` to the SQLAlchemy imports, then add above `class Lead`:

```python
ROLE_USER = "user"
ROLE_ADMIN = "admin"


class User(Base):
    """An authenticated person.

    `role` is TEXT rather than a Postgres enum: extending an enum requires a
    migration, and a future 'viewer' or 'owner' role should not need DDL.
    Validity is enforced in the application layer.
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_sub = Column(String, unique=True, index=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String)
    picture_url = Column(String)
    role = Column(String, nullable=False, default=ROLE_USER)
    # Fernet ciphertext of the Gmail refresh token — never a plaintext token.
    gmail_refresh_token_enc = Column(LargeBinary)
    gmail_sender_email = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_user_model.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Apply the migration locally**

```bash
psql "postgresql://cold_email:secret@localhost:5432/cold_email" -f migrations/005_users.sql
```

- [ ] **Step 7: Commit**

```bash
git add migrations/005_users.sql cold_email/database.py tests/test_user_model.py
git commit -m "feat(auth): add users table with encrypted Gmail refresh token"
```

---

### Task 5: Google OAuth client

**Files:**
- Create: `cold_email/auth/google_oauth.py`
- Test: `tests/test_google_oauth.py`

**Interfaces:**
- Consumes: `settings.gmail_client_id`, `settings.gmail_client_secret`, `settings.google_redirect_uri`; `session.mint_state`
- Produces: `GOOGLE_SCOPES: list[str]`, `build_authorize_url(state: str) -> str`, `exchange_code(code: str) -> GoogleIdentity`, `GoogleIdentity` dataclass with fields `sub`, `email`, `name`, `picture_url`, `refresh_token: str | None`, and `OAuthExchangeFailed`

- [ ] **Step 1: Write the failing test**

Create `tests/test_google_oauth.py`:

```python
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
    "name": "A Person",
    "picture": "https://example.com/p.jpg",
}


def _id_token() -> str:
    import jwt

    return jwt.encode(FAKE_ID_TOKEN_CLAIMS, "irrelevant", algorithm="HS256")


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

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: handler(None)
    )

    identity = exchange_code("auth-code")
    assert identity.sub == "1234567890"
    assert identity.email == "person@example.com"
    assert identity.name == "A Person"
    assert identity.picture_url == "https://example.com/p.jpg"
    assert identity.refresh_token == "rt-secret"


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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_google_oauth.py -v`
Expected: FAIL — `ModuleNotFoundError: cold_email.auth.google_oauth`

- [ ] **Step 3: Implement it**

Create `cold_email/auth/google_oauth.py`:

```python
"""Google OAuth2 authorization-code flow — the only module that talks to Google.

One consent screen yields both identity (openid/email/profile) and send
capability (gmail.compose), so login and Gmail connection are a single step.

The existing Google Cloud OAuth client is reused: gmail_client_id and
gmail_client_secret identify this *application* and are required to refresh any
user's token. They are app-level, not per-user.
"""

import logging
import urllib.parse
from dataclasses import dataclass

import httpx
import jwt

from cold_email.config import settings

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint, not a secret)

GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.compose",
]

TOKEN_TIMEOUT_SECONDS = 15


class OAuthExchangeFailed(RuntimeError):
    """Google rejected the authorization code, or returned an unusable response."""


@dataclass(frozen=True)
class GoogleIdentity:
    """What a successful code exchange tells us about the user."""

    sub: str
    email: str
    name: str | None
    picture_url: str | None
    refresh_token: str | None


def build_authorize_url(state: str) -> str:
    """Build the consent-screen URL the browser is sent to.

    access_type=offline requests a refresh token; prompt=consent forces Google
    to return one even for a user who has consented before. Without it, only a
    user's first-ever authorization yields a refresh token, so a re-signup
    produces an account that cannot send.
    """
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> GoogleIdentity:
    """Exchange an authorization code for identity claims and a refresh token."""
    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=TOKEN_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise OAuthExchangeFailed(f"Token endpoint unreachable: {exc}") from exc

    if response.status_code != 200:
        # Log the status and Google's error code, never the authorization code.
        logger.error(f"Google token exchange failed: {response.status_code} {response.text[:200]}")
        raise OAuthExchangeFailed(f"Google returned {response.status_code}")

    payload = response.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise OAuthExchangeFailed("Google response contained no id_token")

    # The id_token came over TLS straight from Google's token endpoint in
    # response to our client_secret, so its claims are trusted without
    # re-verifying the signature. (Signature verification would be required if
    # the token arrived from the client instead.)
    claims = jwt.decode(id_token, options={"verify_signature": False})

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise OAuthExchangeFailed("id_token missing sub or email")

    return GoogleIdentity(
        sub=sub,
        email=email,
        name=claims.get("name"),
        picture_url=claims.get("picture"),
        # Absent when the user has consented before — a send problem, not a
        # login problem. The caller surfaces it as gmail_connected: false.
        refresh_token=payload.get("refresh_token"),
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_google_oauth.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add cold_email/auth/google_oauth.py tests/test_google_oauth.py
git commit -m "feat(auth): add Google OAuth authorize URL and code exchange"
```

---

### Task 6: FastAPI auth dependencies

**Files:**
- Create: `cold_email/auth/deps.py`
- Modify: `cold_email/auth/__init__.py`
- Test: `tests/test_auth_deps.py`

**Interfaces:**
- Consumes: `session.verify_session`, `session.SESSION_COOKIE`, `database.User`, `database.get_async_session`
- Produces: `get_current_user(...) -> User` (401), `require_admin(...) -> User` (403)

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_deps.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_auth_deps.py -v`
Expected: FAIL — `ModuleNotFoundError: cold_email.auth.deps`

- [ ] **Step 3: Implement it**

Create `cold_email/auth/deps.py`:

```python
"""FastAPI dependencies — the only auth surface routes should import.

Two dependencies express the whole policy:
  * get_current_user — authenticated, else 401
  * require_admin    — authenticated AND role='admin', else 403

Routes never parse a JWT or read a cookie themselves, so the session format is
free to change without touching a single route.
"""

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.session import SESSION_COOKIE, verify_session
from cold_email.database import User, get_async_session


async def get_current_user(
    ce_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Resolve the caller, or 401.

    Every invalid-token case collapses to the same answer — not logged in —
    so there is no need to distinguish expired from tampered from absent. A
    session whose user row no longer exists is also 401, not a 500.
    """
    user_id = verify_session(ce_session)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Resolve the caller and require the admin role, else 403.

    403 rather than 401: the caller is authenticated, just not authorized.
    Returning 401 would tell a legitimate user to log in again, which cannot fix
    the problem.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required"
        )
    return user
```

- [ ] **Step 4: Re-export from the package**

Append to `cold_email/auth/__init__.py`:

```python
from cold_email.auth.deps import get_current_user, require_admin

__all__ = ["get_current_user", "require_admin"]
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_auth_deps.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add cold_email/auth/deps.py cold_email/auth/__init__.py tests/test_auth_deps.py
git commit -m "feat(auth): add get_current_user and require_admin dependencies"
```

---

### Task 7: Auth routes

**Files:**
- Create: `cold_email/api/routes/auth.py`
- Modify: `cold_email/api/routes/api.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `google_oauth.build_authorize_url`, `google_oauth.exchange_code`, `session.mint_session`, `session.mint_state`, `session.verify_state`, `crypto.encrypt`, `database.User`
- Produces: `GET /api/auth/google/login`, `GET /api/auth/google/callback`, `GET /api/auth/me`, `POST /api/auth/logout`; helper `upsert_user(session, identity) -> User`

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth.py`:

```python
import jwt
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
    assert 'ce_session=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers[
        "set-cookie"
    ]
```

- [ ] **Step 2: Add the test fixtures**

Append to `tests/conftest.py`:

```python
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cold_email.api.main import app
from cold_email.auth.session import SESSION_COOKIE, mint_session
from cold_email.database import ROLE_ADMIN, ROLE_USER, User, get_async_session


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
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: cold_email.api.routes.auth`

- [ ] **Step 4: Implement the routes**

Create `cold_email/api/routes/auth.py`:

```python
"""Google Sign-In routes.

One consent flow yields identity and Gmail send capability together. The
callback is the only place a Google refresh token is ever written, and it is
encrypted before it touches the database.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.crypto import encrypt
from cold_email.auth.deps import get_current_user
from cold_email.auth.google_oauth import (
    GoogleIdentity,
    OAuthExchangeFailed,
    build_authorize_url,
    exchange_code,
)
from cold_email.auth.session import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    mint_session,
    mint_state,
    verify_state,
)
from cold_email.config import settings
from cold_email.database import User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def upsert_user(session: AsyncSession, identity: GoogleIdentity) -> User:
    """Create or update the user behind a Google identity.

    Matched on google_sub first (stable forever), then by email to claim a row
    seeded before that person's first sign-in. The seeded row's `role` is
    preserved — silently demoting the only admin would lock discovery and
    research away from everyone.
    """
    user = (
        await session.execute(select(User).where(User.google_sub == identity.sub))
    ).scalar_one_or_none()

    if user is None:
        user = (
            await session.execute(select(User).where(User.email == identity.email))
        ).scalar_one_or_none()

    if user is None:
        user = User(email=identity.email)
        session.add(user)

    user.google_sub = identity.sub
    user.email = identity.email
    user.name = identity.name
    user.picture_url = identity.picture_url

    # Only overwrite when Google actually returned one. A re-login that omits
    # refresh_token must not wipe a working token.
    if identity.refresh_token:
        user.gmail_refresh_token_enc = encrypt(identity.refresh_token)
        user.gmail_sender_email = identity.email

    await session.commit()
    await session.refresh(user)
    return user


@router.get("/google/login")
async def google_login():
    """Return the consent-screen URL, carrying a signed CSRF state nonce."""
    return {"authorize_url": build_authorize_url(mint_state())}


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_async_session),
):
    """Complete the flow: verify state, exchange the code, set the session."""
    if not verify_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
        )

    try:
        identity = exchange_code(code)
    except OAuthExchangeFailed as exc:
        logger.warning(f"OAuth exchange failed: {exc}")
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=oauth_failed",
            status_code=status.HTTP_302_FOUND,
        )

    user = await upsert_user(session, identity)

    response = RedirectResponse(url=settings.frontend_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=mint_session(user.id),
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,          # unreadable by JavaScript, so an XSS cannot steal it
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        path="/",
    )
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """The caller's identity and connection state."""
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "picture_url": user.picture_url,
        "role": user.role,
        "gmail_connected": user.gmail_refresh_token_enc is not None,
    }


@router.post("/logout")
async def logout(response: Response, user: User = Depends(get_current_user)):
    """Clear the session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE, path="/", secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
    )
    return {"success": True}
```

- [ ] **Step 5: Register the router**

In `cold_email/api/routes/api.py`, add `auth` to the import and include it first:

```python
from cold_email.api.routes import auth, dlq, leads, pipeline, system

router = APIRouter(prefix="/api")

router.include_router(auth.router)
router.include_router(system.router)
router.include_router(leads.router)
router.include_router(pipeline.router)
router.include_router(dlq.router)
```

- [ ] **Step 6: Run it to verify it passes**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS (12 tests)

- [ ] **Step 7: Commit**

```bash
git add cold_email/api/routes/auth.py cold_email/api/routes/api.py tests/test_auth.py tests/conftest.py
git commit -m "feat(auth): add Google Sign-In routes and session cookie"
```

---

### Task 8: Gate the routes

**Files:**
- Modify: `cold_email/api/routes/pipeline.py`
- Modify: `cold_email/api/routes/leads.py`
- Modify: `cold_email/api/routes/dlq.py`
- Test: `tests/test_auth_gating.py`

**Interfaces:**
- Consumes: `get_current_user`, `require_admin`
- Produces: no new symbols; every route except `/api/health` and `/api/auth/google/*` now requires a session

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_gating.py`:

```python
import pytest

ADMIN_ONLY = [
    "/api/pipeline/discovery",
    "/api/pipeline/research",
    "/api/pipeline/drafting",
]

USER_ROUTES = [
    ("GET", "/api/leads"),
    ("GET", "/api/leads/drafts"),
    ("GET", "/api/pipeline/stats"),
    ("GET", "/api/dlq"),
]


@pytest.mark.asyncio
async def test_health_stays_public(client):
    """Cloud Run's health check is unauthenticated. Gating this takes
    production down, so it is a regression guard."""
    assert (await client.get("/api/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_reject_anonymous(client, path):
    assert (await client.post(path)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_reject_plain_users(user_client, path):
    assert (await user_client.post(path)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_accept_admins(admin_client, path, monkeypatch):
    # Celery is not running in tests; the routes already tolerate a broker
    # failure and return a null task_id.
    assert (await admin_client.post(path)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", USER_ROUTES)
async def test_user_routes_reject_anonymous(client, method, path):
    response = await client.request(method, path)
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", USER_ROUTES)
async def test_user_routes_accept_authenticated_users(user_client, method, path):
    response = await user_client.request(method, path)
    assert response.status_code == 200
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_auth_gating.py -v`
Expected: FAIL — the anonymous tests return 200 because nothing is gated yet.

- [ ] **Step 3: Gate the pipeline routes**

In `cold_email/api/routes/pipeline.py`, add the imports:

```python
from cold_email.auth.deps import get_current_user, require_admin
from cold_email.database import Lead, User, get_async_session
```

Then add a dependency to each route signature:

```python
@router.get("/stats")
async def get_pipeline_stats(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
```

```python
@router.post("/discovery")
async def trigger_discovery_api(admin: User = Depends(require_admin)):
```

```python
@router.post("/drafting")
async def trigger_drafting_api(admin: User = Depends(require_admin)):
```

```python
@router.post("/research")
async def trigger_research_api(
    session: AsyncSession = Depends(get_async_session),
    admin: User = Depends(require_admin),
):
```

- [ ] **Step 4: Gate the leads and DLQ routes**

Add `user: User = Depends(get_current_user)` to every route in
`cold_email/api/routes/leads.py` and `cold_email/api/routes/dlq.py`, importing:

```python
from cold_email.auth.deps import get_current_user
from cold_email.database import Lead, User, get_async_session
```

These routes are login-gated but **not yet user-scoped** — scoping arrives in
Stack 1b, when `outreach` exists to scope by. Add this comment above the first
gated route in `leads.py`:

```python
# Login-gated in Stack 1a; user-SCOPED in Stack 1b once `outreach` exists.
# Until then every authenticated user sees the same leads.
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_auth_gating.py -v`
Expected: PASS (19 tests)

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Existing `tests/test_api.py` tests will now fail with 401 — update them to use the `user_client` fixture instead of an unauthenticated client.

- [ ] **Step 7: Commit**

```bash
git add cold_email/api/routes/pipeline.py cold_email/api/routes/leads.py cold_email/api/routes/dlq.py tests/test_auth_gating.py tests/test_api.py
git commit -m "feat(auth): gate all routes; discovery and research are admin-only

/api/health stays public — Cloud Run's health check is unauthenticated."
```

---

### Task 9: Admin seeding on boot

**Files:**
- Modify: `scripts/start.sh`
- Create: `scripts/seed_admin.py`
- Test: `tests/test_seed_admin.py`

**Interfaces:**
- Consumes: `settings.admin_email`, `database.User`
- Produces: `seed_admin() -> None` (idempotent)

- [ ] **Step 1: Write the failing test**

Create `tests/test_seed_admin.py`:

```python
import pytest
from sqlalchemy import select

from cold_email.database import ROLE_ADMIN, User
from scripts.seed_admin import seed_admin


@pytest.mark.asyncio
async def test_seeds_an_admin_when_absent(async_session, monkeypatch):
    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)

    user = (
        await async_session.execute(select(User).where(User.email == "boss@example.com"))
    ).scalar_one()
    assert user.role == ROLE_ADMIN
    assert user.google_sub is None


@pytest.mark.asyncio
async def test_is_idempotent(async_session, monkeypatch):
    """start.sh runs this on every boot, so a second call must not duplicate."""
    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)
    await seed_admin(async_session)

    users = (await async_session.execute(select(User))).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_promotes_an_existing_user(async_session, monkeypatch):
    async_session.add(User(email="boss@example.com", google_sub="sub-x"))
    await async_session.commit()

    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)

    user = (
        await async_session.execute(select(User).where(User.email == "boss@example.com"))
    ).scalar_one()
    assert user.role == ROLE_ADMIN
    assert user.google_sub == "sub-x"


@pytest.mark.asyncio
async def test_no_admin_email_is_a_noop(async_session, monkeypatch):
    monkeypatch.setattr("cold_email.config.settings.admin_email", "")
    await seed_admin(async_session)
    assert (await async_session.execute(select(User))).scalars().all() == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_seed_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.seed_admin`

- [ ] **Step 3: Implement it**

Create `scripts/seed_admin.py`:

```python
"""Idempotently ensure ADMIN_EMAIL exists with role='admin'.

Run on every boot by start.sh. The seeded row has a NULL google_sub; the OAuth
callback claims it by email on that person's first sign-in and preserves the
admin role.

Without this, a fresh deployment has no admin, so nobody can trigger discovery
or research and the pool never fills.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.config import settings
from cold_email.database import ROLE_ADMIN, AsyncSessionLocal, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed_admin(session: AsyncSession) -> None:
    """Create or promote the configured admin. Safe to call repeatedly."""
    email = settings.admin_email
    if not email:
        logger.info("ADMIN_EMAIL unset; skipping admin seed")
        return

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user is None:
        session.add(User(email=email, role=ROLE_ADMIN))
        logger.info(f"Seeded admin user {email}")
    elif user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
        logger.info(f"Promoted {email} to admin")
    else:
        logger.info(f"Admin {email} already present")

    await session.commit()


async def _main() -> None:
    async with AsyncSessionLocal() as session:
        await seed_admin(session)


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_seed_admin.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire it into boot**

In `scripts/start.sh`, after the existing `Base.metadata.create_all` provisioning
step, add:

```bash
echo "Seeding admin user..."
python -m scripts.seed_admin || echo "WARNING: admin seed failed; continuing"
```

The `||` guard is deliberate: a failed seed must not prevent the container from
starting, or a transient database blip becomes a total outage.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_admin.py scripts/start.sh tests/test_seed_admin.py
git commit -m "feat(auth): seed the admin user idempotently on boot"
```

---

### Task 10: Split the frontend and add auth

**Files:**
- Create: `frontend/lib/auth.tsx`
- Create: `frontend/app/login/page.tsx`
- Create: `frontend/components/ReviewDeck.tsx`
- Create: `frontend/components/LeadExplorer.tsx`
- Create: `frontend/components/PipelineStats.tsx`
- Create: `frontend/components/AdminPanel.tsx`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/lib/api.ts`

**Interfaces:**
- Consumes: `GET /api/auth/me`, `GET /api/auth/google/login`, `POST /api/auth/logout`
- Produces: `AuthProvider`, `useAuth() -> {user, loading, logout}`, and the four extracted components

- [ ] **Step 1: Send credentials on every request**

In `frontend/lib/api.ts`, add `credentials: 'include'` to every `fetch` call and
add a 401 handler. Cookies are not sent cross-origin without it, so every request
would arrive anonymous:

```typescript
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: 'include',  // required: cookies are not sent cross-origin by default
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });

  if (response.status === 401 && typeof window !== 'undefined') {
    window.location.href = '/login';
    throw new Error('Not authenticated');
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}
```

Route every existing exported function through `request`.

- [ ] **Step 2: Create the auth context**

Create `frontend/lib/auth.tsx`:

```tsx
'use client';

import { createContext, useContext, useEffect, useState } from 'react';

export type User = {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  role: 'user' | 'admin';
  gmail_connected: boolean;
};

type AuthState = { user: User | null; loading: boolean; logout: () => Promise<void> };

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  logout: async () => {},
});

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_URL}/api/auth/me`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const logout = async () => {
    await fetch(`${API_URL}/api/auth/logout`, { method: 'POST', credentials: 'include' });
    setUser(null);
    window.location.href = '/login';
  };

  return (
    <AuthContext.Provider value={{ user, loading, logout }}>{children}</AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
```

- [ ] **Step 3: Create the login page**

Create `frontend/app/login/page.tsx`:

```tsx
'use client';

import { useSearchParams } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '';

export default function LoginPage() {
  const error = useSearchParams().get('error');

  const signIn = async () => {
    const res = await fetch(`${API_URL}/api/auth/google/login`, { credentials: 'include' });
    const { authorize_url } = await res.json();
    window.location.href = authorize_url;
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6">
      <h1 className="text-2xl font-semibold">Cold Email Agent</h1>
      {error === 'oauth_failed' && (
        <p className="text-red-600">Sign-in failed. Please try again.</p>
      )}
      <button onClick={signIn} className="rounded border px-6 py-3">
        Sign in with Google
      </button>
      <p className="max-w-sm text-center text-sm text-gray-500">
        We request Gmail access so drafts are created and sent from your own mailbox.
      </p>
    </main>
  );
}
```

- [ ] **Step 4: Extract the four components**

Move the existing JSX out of `frontend/app/page.tsx` verbatim — no behaviour
changes — into:

- `components/PipelineStats.tsx` — the stats header
- `components/ReviewDeck.tsx` — the draft review UI
- `components/LeadExplorer.tsx` — the lead table
- `components/AdminPanel.tsx` — the discovery/research/drafting trigger buttons

Each takes its data as props and is a `'use client'` component.

- [ ] **Step 5: Reduce `page.tsx` to an auth gate**

```tsx
'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

import AdminPanel from '@/components/AdminPanel';
import LeadExplorer from '@/components/LeadExplorer';
import PipelineStats from '@/components/PipelineStats';
import ReviewDeck from '@/components/ReviewDeck';
import { useAuth } from '@/lib/auth';

export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.push('/login');
  }, [loading, user, router]);

  if (loading) return <main className="p-8">Loading…</main>;
  if (!user) return null;

  return (
    <main className="p-8">
      <PipelineStats />
      {/* Cosmetic only. require_admin on the backend is the real boundary —
          hiding a button is not authorization. */}
      {user.role === 'admin' && <AdminPanel />}
      <ReviewDeck />
      <LeadExplorer />
    </main>
  );
}
```

- [ ] **Step 6: Wrap the app in the provider**

In `frontend/app/layout.tsx`, wrap `{children}` in `<AuthProvider>`.

- [ ] **Step 7: Verify the build**

```bash
cd frontend && npm run build
```
Expected: build succeeds with no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat(auth): add Google sign-in UI and split page.tsx into components

page.tsx was 917 lines and is about to absorb a pool browser, a profile
editor, and scheduling. Split now rather than growing it further."
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Consumes: everything above
- Produces: no code

- [ ] **Step 1: Update `CLAUDE.md`**

Add an "Authentication & Roles" section after the architecture diagram covering:
the Google flow, the reused OAuth client, the app-level vs user-level credential
split, `role` gating, and the session cookie. Add the four `/api/auth/*` endpoints
to the endpoint table and mark discovery/research/drafting **admin-only**. Add
`SESSION_SECRET`, `ENCRYPTION_KEY`, `GOOGLE_REDIRECT_URI`, `FRONTEND_URL`,
`ADMIN_EMAIL`, `COOKIE_SECURE` to the env block, and note that `CORS_ORIGINS`
must be explicit.

- [ ] **Step 2: Update `README.md`**

Add a "Google OAuth setup" section: which scopes to add to the existing OAuth
client, the redirect URI to register, the two key-generation commands, and the
`ADMIN_EMAIL` seed.

- [ ] **Step 3: Update `docs/DEPLOYMENT.md`**

Add the two Secret Manager entries and the Cloud Run wiring:

```bash
gcloud secrets create session-secret --data-file=-
gcloud secrets create encryption-key --data-file=-

gcloud run deploy cold-email-backend \
  --update-secrets=SESSION_SECRET=session-secret:latest,ENCRYPTION_KEY=encryption-key:latest \
  --set-env-vars=GOOGLE_REDIRECT_URI=...,FRONTEND_URL=...,ADMIN_EMAIL=...
```

Include the warning: **`ENCRYPTION_KEY` must be backed up before any user signs
in.** Losing it makes every stored Gmail refresh token undecryptable and forces
every user to re-consent.

- [ ] **Step 4: Full verification**

```bash
uv run pytest
uv run ruff check .
cd frontend && npm run build
```
Expected: all pass.

- [ ] **Step 5: Commit and open the PR**

```bash
git add CLAUDE.md README.md docs/DEPLOYMENT.md
git commit -m "docs: document authentication, roles, and new secrets"
git push -u origin feat/tenancy-auth
gh pr create --base main --title "Stack 1a: authentication and roles" \
  --body "Implements docs/superpowers/specs/2026-08-14-stack-1a-auth-design.md

Google Sign-In, users table with encrypted per-user Gmail refresh tokens,
session cookies, and admin/user role gating. The data model is untouched —
\`leads\` and the pipeline behave exactly as before.

Also fixes a live bug: \`cors_origins = [\"*\"]\` with \`allow_credentials=True\`
is rejected by browsers, so cookie sessions would have silently failed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage.** Every section of the 1a spec maps to a task: the OAuth flow
(5, 7), the `auth/` package's four modules (2, 3, 5, 6), the CORS fix (1), the
`users` table (4), admin seeding (9), the API surface (7, 8), route gating (8),
configuration (1), the frontend split (10), error handling (5, 6, 7 — each case
in the spec's table has a test), and documentation (11).

**Placeholder scan.** No TBDs. Every code step carries the actual code; every
test step carries the actual assertions.

**Type consistency.** `GoogleIdentity` is defined in Task 5 with fields `sub`,
`email`, `name`, `picture_url`, `refresh_token` and constructed with exactly those
in Tasks 5 and 7. `mint_session`/`verify_session`/`mint_state`/`verify_state`
signatures in Task 3 match their uses in Tasks 6 and 7. `SESSION_COOKIE` is
defined once in Task 3 and imported everywhere. `ROLE_USER`/`ROLE_ADMIN` are
defined in Task 4 and used in Tasks 6, 7, and 9. `encrypt`/`decrypt` from Task 2
are used in Task 7.

**One deliberate incompleteness:** Tasks 8's `leads`/`dlq` routes are login-gated
but not user-scoped, because `outreach` does not exist yet. This is marked with a
code comment naming Stack 1b, and it is stated in the spec's API table.
