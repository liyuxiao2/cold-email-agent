# Stack 1a — Tenancy Foundation: Authentication & Roles

_Date: 2026-08-14_
_Branch: `feat/tenancy-auth` (base: `main`)_
_Parent spec: [Multi-Tenant Revamp Overview](2026-08-14-multi-tenant-revamp-overview-design.md)_

## Goal

Introduce identity. After this stack the API knows *who* is calling, stores each
user's Gmail refresh token encrypted, and refuses discovery and research to
non-admins. The data model is untouched — `leads` still exists and the pipeline
still behaves exactly as before. Splitting the schema is Stack 1b's job.

Shipping auth alone is deliberate: it does not depend on the schema split, and
merging the two produces a diff nobody can review.

## Why one OAuth flow, not two

Google's authorization-code flow returns identity claims *and* a refresh token
carrying whatever scopes were consented to. Requesting
`openid email profile https://www.googleapis.com/auth/gmail.compose` in a single
consent screen gets login and send capability together.

The **existing Google Cloud OAuth client is reused** — `gmail_client_id` and
`gmail_client_secret` already identify this application. It needs two additions
in the Google Cloud console:

1. The `openid`, `email`, and `profile` scopes on the consent screen.
2. A web redirect URI: `https://<backend>/api/auth/google/callback`.

`access_type=offline` and `prompt=consent` are both required. Without
`prompt=consent`, Google returns a refresh token only on a user's *first ever*
authorization, so re-signups silently produce a user who cannot send.

## Architecture

```
Browser                        Backend (Cloud Run)              Google
   │                                  │                            │
   │ GET /api/auth/google/login       │                            │
   ├─────────────────────────────────▶│                            │
   │ ◀── 200 {authorize_url}          │                            │
   │                                  │                            │
   │ ── redirect to authorize_url ────┼───────────────────────────▶ │
   │                                  │        (user consents)     │
   │ ◀───────── redirect with ?code ──┼─────────────────────────── │
   │                                  │                            │
   │ GET /api/auth/google/callback    │                            │
   ├─────────────────────────────────▶│  POST /token (code)        │
   │                                  ├──────────────────────────▶ │
   │                                  │ ◀── id_token + refresh_tkn │
   │                                  │                            │
   │                                  │  upsert user by google_sub │
   │                                  │  Fernet-encrypt refresh    │
   │                                  │  mint session JWT          │
   │ ◀── 302 to frontend              │                            │
   │     Set-Cookie: ce_session       │                            │
```

### New package: `cold_email/auth/`

| Module | Responsibility |
|---|---|
| `crypto.py` | Fernet wrapper: `encrypt(str) -> bytes`, `decrypt(bytes) -> str`. Key from `settings.encryption_key`. The single place secrets are enciphered. |
| `google_oauth.py` | `build_authorize_url(state)`, `exchange_code(code) -> GoogleIdentity`. Owns all Google HTTP contact. |
| `session.py` | `mint_session(user_id) -> str`, `verify_session(token) -> UUID`. HS256 JWT, 7-day expiry, `settings.session_secret`. |
| `deps.py` | `get_current_user` (401 if absent/invalid) and `require_admin` (403 if `role != 'admin'`). The only two things routes import. |

Each module has one purpose and no knowledge of the others' internals:
`deps.py` never parses a JWT itself, `google_oauth.py` never touches the database.

### Why a JWT in an httpOnly cookie

Alternatives were a Bearer token in `localStorage` (readable by any XSS, so a
single injected script exfiltrates a session) and server-side sessions in Redis
(a DB round-trip per request, and Redis is currently a *broker*, not a store —
losing it would log everyone out).

A stateless HS256 JWT in an `httpOnly` cookie is unreadable by JavaScript and
needs no per-request lookup. The tradeoff — you cannot revoke an individual
token before expiry — is acceptable at a 7-day expiry with no billing or
destructive admin actions yet. If revocation becomes necessary, add a
`session_version` integer to `users` and embed it in the claim.

### ⚠️ The CORS bug this stack must fix

`config.py` sets `cors_origins = ["*"]` and `main.py` sets
`allow_credentials=True`. **Browsers reject that combination outright** — the
CORS spec forbids a wildcard `Access-Control-Allow-Origin` on credentialed
requests. Cookie-based sessions from Vercel to Cloud Run would fail with no
useful error until the origin list is explicit.

Cross-origin cookies additionally require `SameSite=None; Secure`, which means
sessions only work over HTTPS. Local development therefore needs
`http://localhost:3000` in the origin list and a `cookie_secure: bool = True`
setting that is flipped off locally.

## Data model

```sql
-- migrations/005_users.sql
CREATE TABLE users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub              TEXT UNIQUE,          -- Google's stable subject id
    email                   TEXT UNIQUE NOT NULL,
    name                    TEXT,
    picture_url             TEXT,
    role                    TEXT NOT NULL DEFAULT 'user',   -- user | admin
    gmail_refresh_token_enc BYTEA,                -- Fernet ciphertext
    gmail_sender_email      TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX users_google_sub_idx ON users (google_sub);
```

`google_sub` is nullable so the admin row can be seeded by email before that
person's first login; the callback fills it in on first sign-in and matches on it
thereafter. Matching on `google_sub` rather than `email` is deliberate — Google
account emails can change, but `sub` never does.

`role` is a `TEXT` column, not a Postgres enum. Enums require a migration to
extend; a future `viewer` or `owner` role should not need DDL. Validity is
enforced in the ORM layer.

### Seeding admin user #1

`scripts/start.sh` already provisions tables idempotently on boot. It gains an
idempotent upsert reading `settings.admin_email`: if no user exists with that
email, insert one with `role='admin'`. On Liyu's first Google sign-in the
callback matches by email, fills `google_sub`, and preserves the admin role.

## API surface

| Endpoint | Auth | Behaviour |
|---|---|---|
| `GET /api/auth/google/login` | public | Returns `{authorize_url}` with a signed `state` nonce |
| `GET /api/auth/google/callback?code&state` | public | Verifies state, exchanges code, upserts user, sets `ce_session`, 302s to `settings.frontend_url` |
| `GET /api/auth/me` | user | Returns `{id, email, name, picture_url, role, gmail_connected}` |
| `POST /api/auth/logout` | user | Clears the cookie |
| `POST /api/pipeline/discovery` | **admin** | unchanged behaviour, now gated |
| `POST /api/pipeline/research` | **admin** | unchanged behaviour, now gated |
| `POST /api/pipeline/drafting` | **admin** | gated for now; Stack 3 replaces it with a per-user endpoint |
| `GET /api/health` | public | must stay public for Cloud Run health checks |
| everything else (`/leads/*`, `/dlq/*`, `/pipeline/stats`) | user | login required, not yet user-*scoped* — that is Stack 1b |

The `state` nonce is a short-lived signed token, not a random value in a session
store, so it needs no server-side storage. It prevents CSRF on the callback: an
attacker who cannot mint a signed `state` cannot force a victim's browser to
complete an authorization the attacker initiated.

## Configuration

New settings in `config.py`:

```python
session_secret: str = ""            # HS256 signing key
encryption_key: str = ""            # Fernet key (44-char urlsafe base64)
google_redirect_uri: str = ""       # must exactly match the console entry
frontend_url: str = "http://localhost:3000"
admin_email: str = ""               # seeded as role='admin' on boot
cookie_secure: bool = True          # False only for local http
cors_origins: list[str] = [         # CHANGED: no longer ["*"]
    "http://localhost:3000",
]
```

`session_secret` and `encryption_key` go in Secret Manager and are wired to
Cloud Run as env vars. `settings.gmail_refresh_token` and
`settings.gmail_sender_email` become **unused** in this stack — they are removed
in Stack 2 when `gmail_client` starts taking per-user credentials, so the
pipeline keeps working in between.

⚠️ **`encryption_key` is unrecoverable.** Losing or rotating it makes every
stored Gmail refresh token undecryptable and forces every user to re-consent.
It must be created once and backed up before any user signs in.

## Frontend

`frontend/app/page.tsx` is 917 lines and is about to absorb login, a pool
browser, a profile editor, and scheduling. It gets split here rather than later,
because every subsequent stack would otherwise add to an already-oversized file:

```
frontend/
  app/
    layout.tsx           # wraps children in AuthProvider
    page.tsx             # thin: auth gate → <ReviewDeck/> or <Login/>
    login/page.tsx       # "Sign in with Google" button
  components/
    ReviewDeck.tsx       # the existing draft review UI, extracted
    LeadExplorer.tsx     # the existing lead table, extracted
    PipelineStats.tsx    # the existing stats header, extracted
    AdminPanel.tsx       # discovery/research triggers, rendered only for role='admin'
  lib/
    api.ts               # all fetches gain credentials: 'include'; 401 → redirect to login
    auth.tsx             # AuthContext: {user, loading}, fetches /api/auth/me
```

`AdminPanel` renders conditionally on `role`, but that is cosmetic only — the
backend `require_admin` dependency is the actual boundary. Client-side role
checks hide buttons; they never authorize.

## Error handling

| Condition | Response |
|---|---|
| No cookie, or malformed/expired JWT | `401` |
| Valid session, user row deleted | `401` (treat as logged out) |
| Authenticated but `role != 'admin'` on a gated route | `403` |
| Google token exchange fails | `302` to `{frontend_url}/login?error=oauth_failed` |
| Google returns no refresh token | user is created, `gmail_connected: false`; the UI prompts re-consent |
| Invalid or missing `state` | `400`, no user created |
| `encryption_key` unset at startup | **fail fast at import** — do not boot an app that will write unencrypted tokens |

The refresh-token-missing case matters: it is not an error for *login*, only for
*sending*. The user gets an account and a clear "Reconnect Gmail" prompt rather
than a dead-end failure.

## Testing

`tests/test_auth.py` — all Google HTTP mocked at the `google_oauth` boundary:

- Fernet round-trip; distinct ciphertexts for the same plaintext (Fernet is
  randomized), both decrypting correctly.
- JWT mint → verify round-trip; expired token rejected; token signed with a
  different secret rejected.
- `build_authorize_url` contains `access_type=offline`, `prompt=consent`, and
  every required scope.
- Callback with a valid code creates a user, stores an **encrypted** token
  (assert the stored bytes are not the plaintext), sets the cookie.
- Callback for an existing `google_sub` updates rather than duplicating.
- Callback matching a seeded admin by email fills `google_sub` and **keeps
  `role='admin'`**.
- Callback with a tampered `state` returns 400 and creates no user.
- Callback whose token response omits `refresh_token` still creates the user.

`tests/test_auth_gating.py`:

- Every gated route unauthenticated → 401.
- Discovery, research, drafting as `role='user'` → 403.
- The same three as `role='admin'` → 200.
- `GET /api/health` unauthenticated → 200 (regression guard: breaking this
  breaks Cloud Run's health check and takes production down).

`tests/conftest.py` gains `user_client` and `admin_client` fixtures that mint a
real session cookie, so gating tests exercise the actual dependency chain rather
than a monkeypatched stub.

## Documentation updated in this stack

- `CLAUDE.md` — new "Authentication & Roles" section; auth endpoints added to the
  endpoint table; new env vars in the secrets block; the CORS change noted.
- `README.md` — Google OAuth client setup steps (scopes, redirect URI), how to
  generate `session_secret` and `encryption_key`, and the `admin_email` seed.
- `docs/DEPLOYMENT.md` — Secret Manager entries for the two new secrets and the
  Cloud Run wiring, plus the `encryption_key` backup warning.

## Out of scope for 1a

The schema split, user-scoping of `/leads` queries, per-user Gmail *sending*
(`gmail_client` still reads the global token), résumé handling, quotas, and
scheduling. `leads` is untouched and the pipeline behaves exactly as it does
today.
