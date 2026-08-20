# Stack 2 — Sender Identity: Résumé, Profile & Per-User Gmail

_Date: 2026-08-14_
_Branch: `feat/sender-identity` (base: `feat/tenancy-data-model`)_
_Parent spec: [Multi-Tenant Revamp Overview](2026-08-14-multi-tenant-revamp-overview-design.md)_

## Goal

Remove the last compiled-in identity. After this stack every user has their own
résumé, their own profile fields, and sends from their own mailbox — and the
codebase contains no reference to any specific person.

Three single-tenant artifacts die here:

| Artifact | Today | After |
|---|---|---|
| `sender_profile.PROFILE` | frozen module constant, one person | a `profiles` row per user |
| `cold_email/resume.txt`, `resume.pdf` | files committed to the repo | `bytea` on the profile row |
| `settings.gmail_refresh_token` | one mailbox for the whole app | `users.gmail_refresh_token_enc` per user |
| `scripts/gmail_auth.py` | CLI to mint one refresh token | deleted; the web consent flow replaces it |

## The OAuth config split

This is the one thing in this stack that is easy to get backwards, and getting it
backwards means nobody can send.

**App-level, stays in `settings`:**
`gmail_client_id`, `gmail_client_secret`. These identify *the OAuth application*.
Google requires them to refresh **any** user's token — they are not per-user
secrets and must not move to the `users` table.

**User-level, moves to `users` (already added in Stack 1a):**
`gmail_refresh_token_enc`, `gmail_sender_email`. These identify the *mailbox*.

So `settings.gmail_refresh_token` and `settings.gmail_sender_email` are deleted
in this stack; `gmail_client_id` and `gmail_client_secret` remain.

## Data model

```sql
-- migrations/007_profiles.sql
CREATE TABLE profiles (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    intro           TEXT NOT NULL,
    linkedin        TEXT,
    github          TEXT,
    website         TEXT,
    experience_pool JSONB NOT NULL DEFAULT '[]'::jsonb,
    company_links   JSONB NOT NULL DEFAULT '{}'::jsonb,
    resume_pdf      BYTEA,
    resume_filename TEXT,
    resume_text     TEXT,
    parsed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE profiles ALTER COLUMN resume_pdf SET STORAGE EXTERNAL;
```

`user_id` is the primary key — one profile per user, enforced by the schema
rather than by a unique constraint on a surrogate id.

`STORAGE EXTERNAL` tells Postgres to store the value out-of-line **without
attempting compression**. PDFs are already compressed, so the default
`EXTENDED` strategy burns CPU on every write for no size gain.

`experience_pool` and `company_links` are JSONB rather than child tables. They are
always read and written whole, never queried into, and the existing
`SenderProfile` dataclass already models them as a `list[str]` and a `dict[str,
str]`. A child table would add joins and buy nothing.

## Résumé storage: `cold_email/resume_store.py`

The entire read/write surface for PDF bytes, so a future GCS migration is one
implementation swap plus a backfill:

```python
MAX_RESUME_BYTES = 5 * 1024 * 1024          # 5 MB
PDF_MAGIC = b"%PDF-"

def put_resume(session, user_id, filename: str, data: bytes) -> None
def get_resume(session, user_id) -> tuple[str, bytes] | None
def delete_resume(session, user_id) -> None
def validate_resume(filename: str, data: bytes) -> None   # raises ResumeInvalid
```

`validate_resume` enforces both the size cap and the `%PDF-` magic bytes.
Checking magic bytes rather than trusting the `Content-Type` header or the `.pdf`
extension matters because both are attacker-controlled, and `pypdf` on arbitrary
bytes is a parser you do not want to hand untrusted input.

⚠️ **The 5MB cap is not a nicety.** Cloud SQL disk grows automatically and
**never shrinks**. An unbounded upload path permanently inflates the instance —
and its every backup — with no way to reclaim the space short of recreating the
database.

## Résumé → profile extraction

```
POST /api/profile/resume  (multipart)
   │
   ├─ validate_resume       size cap + %PDF- magic bytes
   ├─ put_resume            bytea, same transaction as the row
   ├─ pypdf extract_text    per page, joined
   ├─ generate_json(ResumeProfile)     ← the existing provider-agnostic layer
   │
   ▼
200 {suggested profile}     ← a DRAFT; nothing is committed as final
   │
   ▼
PUT /api/profile            ← user reviews, edits, saves
```

The extraction returns a *suggestion*. It is never written as the authoritative
profile without the user confirming, because the LLM will occasionally mangle a
name or invent a link, and every draft email this user ever sends is built from
these fields.

### New module: `cold_email/profile_extract.py`

```python
def extract_text(pdf_bytes: bytes) -> str          # pypdf, page-joined
def suggest_profile(resume_text: str) -> dict      # generate_json(ResumeProfile)
```

### New prompt: `cold_email/prompts/resume_profile.py`

```python
class ResumeProfile(BaseModel):
    name: str
    intro: str  # one first-person sentence
    linkedin: str | None
    github: str | None
    website: str | None
    experience_pool: list[str]  # 4-8 "Label: achievement" strings
    company_links: dict[str, str]  # {"Wealthsimple": "https://..."}
```

`experience_pool` uses the same `"Label: achievement"` shape the existing
`_bullet_md` parser expects (`label, sep, rest = bullet.partition(": ")`), so the
bold-label-with-link rendering keeps working with no changes to
`html_builder.py`.

`company_links` is the one field the LLM should be conservative about: it must
only emit a URL if the résumé literally contains one. `_bullet_md` degrades
gracefully to a plain bold label when a link is absent, so a missing entry is
harmless while a hallucinated one ships a wrong link to a stranger.

⚠️ **`extract_text` can legitimately return near-nothing** for a
scanned/image-only PDF. That is not a crash — it is a `422` with "we couldn't read
text from this PDF; it may be a scan." Passing empty text to the LLM produces a
confidently fabricated profile, which is far worse than an error message.

## `sender_profile.py` after the change

The dataclass survives; only its source changes.

```python
@dataclass(frozen=True)
class SenderProfile:
    # ... unchanged fields, unchanged first_name / effective_resume_text ...

    @classmethod
    def from_row(cls, row) -> "SenderProfile": ...
```

Deleted: the module-level `PROFILE` constant, `load_resume()`, `_RESUME_PATH`,
and the `resume.txt` / `resume.pdf` files.

`effective_resume_text` is **kept**. Its fallback — synthesising résumé text from
`intro` + `experience_pool` when `resume_text` is empty — is exactly what a user
who fills the profile form manually without uploading a PDF needs.

This is why test churn here is small: `tests/test_email_assembly.py` already
constructs its own `SenderProfile` fixture rather than importing `PROFILE`.

## `gmail_client.py` after the change

```python
@dataclass(frozen=True)
class GmailCredentials:
    refresh_token: str
    sender_email: str

def _build_service(creds: GmailCredentials): ...
def create_draft(creds, to, subject, body, html=None, attachment=None) -> str
def send_draft(creds, draft_id) -> str
```

Two further changes:

- `attachment_path: str | None` becomes `attachment: tuple[str, bytes] | None` —
  `(filename, data)`. There is no file on disk any more; the bytes come from
  `resume_store`. This also removes the runtime `import mimetypes` and
  `from pathlib import Path` from inside `create_draft`, and the résumé is always
  `application/pdf`, so the `mimetypes.guess_type` fallback dance disappears.
- The `Path(__file__).parent.parent.parent / "resume.pdf"` lookup inside
  `drafting_task` — with its "Resume PDF not found" warning — is deleted. A
  missing résumé is now a *data* condition on the profile row, checked before
  drafting, not a filesystem accident discovered mid-loop.

`resolve_gmail_credentials(user) -> GmailCredentials | None` lives in
`cold_email/auth/gmail_creds.py`: it decrypts `gmail_refresh_token_enc` via
`auth.crypto`. Returning `None` when a user has no token keeps the Fernet key
confined to the `auth` package.

## Worker changes

`drafting_task(user_id)` loads, **once per sweep**:

1. the `SenderProfile` (via `profiles`),
2. the résumé bytes (via `resume_store`),
3. the `GmailCredentials` (via `resolve_gmail_credentials`).

Loading once rather than per lead matters: the résumé bytes cross the database
connection on every read, and a 40-lead sweep would otherwise pull ~16MB out of
Cloud SQL to attach the same file 40 times.

Two new preflight checks, both **terminal for the whole sweep, not per lead**,
because neither can be fixed by retrying a different lead:

| Condition | Handling |
|---|---|
| No profile row | Abort the sweep; leave rows `queued`; return `{"status": "no_profile"}` |
| No Gmail credentials | Abort the sweep; leave rows `queued`; return `{"status": "gmail_disconnected"}` |
| Profile exists, no résumé PDF | **Proceed** — draft without an attachment, using `effective_resume_text` |

Leaving rows at `queued` rather than failing them is deliberate: the user
finishing their profile should make those drafts happen, with no DLQ retry needed.

`draft_email(row)` → `draft_email(row, profile)`. `generation.py` stops importing
`PROFILE`; the profile arrives as an argument. `logistics_task` resolves the
credentials for the outreach row's owner before calling `send_draft`.

## API surface

| Endpoint | Auth | Behaviour |
|---|---|---|
| `GET /api/profile` | user | Profile fields + `{has_resume, resume_filename}`. Never returns the bytes. |
| `PUT /api/profile` | user | Upsert profile fields. Validates `name` and `intro` non-empty. |
| `POST /api/profile/resume` | user | Multipart upload → store → parse → return *suggested* profile |
| `GET /api/profile/resume` | user | Download own PDF (`Content-Disposition: attachment`) |
| `DELETE /api/profile/resume` | user | Clears the bytes, keeps the profile fields |
| `GET /api/auth/me` | user | Extended with `{profile_complete, gmail_connected}` |

`GET /api/profile` must not return `resume_pdf`. Beyond payload size, base64-ing
a PDF into every profile fetch means the bytes leave the TOAST table on a request
that only wanted a name — the exact cost `STORAGE EXTERNAL` was chosen to avoid.

`profile_complete` on `/auth/me` drives the frontend's onboarding gate, so the
client needs one request rather than two to decide where to send a new user.

## Frontend

```
app/onboarding/page.tsx      # post-signup: upload résumé → review suggestion → save
app/profile/page.tsx         # edit profile, replace résumé, Gmail connection status
components/ProfileForm.tsx   # shared by both; bullet list add/remove/reorder
components/ResumeUpload.tsx  # drag-drop, client-side 5MB + .pdf pre-check
```

`AuthProvider` gains a redirect: an authenticated user with
`profile_complete: false` lands on `/onboarding`. The client-side size check is a
courtesy that saves a 5MB round trip; `validate_resume` on the server is the
actual enforcement.

The profile page surfaces Gmail connection state and a "Reconnect Gmail" button
that re-runs the consent flow — the recovery path for the Stack 1a case where
Google returned no refresh token.

## Error handling

| Condition | Response |
|---|---|
| Upload > 5MB | `413` |
| Missing `%PDF-` magic bytes | `415` |
| `pypdf` raises on a corrupt PDF | `422`, bytes not stored |
| Extracted text < 100 chars (likely a scan) | `422` "couldn't read text from this PDF" |
| LLM chain exhausted during extraction | `503`; bytes **are** stored, so the user can retry parsing without re-uploading |
| `PUT /api/profile` with empty `name`/`intro` | `422` |
| Drafting sweep, no profile / no Gmail | sweep aborts, rows stay `queued`, no DLQ rows |

The LLM-failure case storing the bytes anyway is the important one: the upload
succeeded and only the *suggestion* failed, so discarding a 5MB upload the user
just waited for would be gratuitous.

## Testing

`tests/test_resume_store.py`

- `put_resume` → `get_resume` round-trips bytes exactly.
- Over-cap and non-`%PDF-` payloads raise `ResumeInvalid`; nothing is stored.
- `delete_resume` clears bytes and leaves profile fields intact.

`tests/test_profile_extract.py` (LLM mocked)

- `extract_text` on a small fixture PDF returns its text.
- Image-only fixture returns near-empty text → the route's `422` path.
- `suggest_profile` maps a `ResumeProfile` payload to the response shape.
- Extracted `experience_pool` entries survive `_bullet_md` parsing — i.e. they
  contain `": "` — the contract that keeps HTML bullet rendering intact.

`tests/test_gmail_client.py`

- `create_draft` builds a multipart message with the plain body, the HTML
  alternative, and a `application/pdf` attachment with the right filename.
- Credentials come from the argument, **not** from `settings` (assert no
  `settings.gmail_refresh_token` access — that regression would silently send
  every user's mail from one mailbox).
- `send_draft` calls `drafts().send()` with the given id.

`tests/test_profile_api.py`

- Unauthenticated → 401 on every profile route.
- **Tenancy isolation:** user A's `GET /api/profile` never returns user B's data;
  `GET /api/profile/resume` returns only the caller's own PDF.
- `GET /api/profile` response contains no `resume_pdf` key.

`tests/test_drafting.py` additions

- No profile → sweep aborts, rows remain `queued`, no dead-letter rows written.
- No Gmail credentials → same.
- Profile without a PDF → drafts successfully, no attachment.
- Profile with a PDF → résumé read exactly **once** for a multi-lead sweep.

## Documentation updated in this stack

- `CLAUDE.md` — the "Sender identity is code, not config" line is now false and
  is replaced by a Sender Identity section; the drafting pipeline description
  loses the repo-`resume.pdf` reference; `GMAIL_REFRESH_TOKEN` and
  `GMAIL_SENDER_EMAIL` are removed from the env block while
  `GMAIL_CLIENT_ID`/`SECRET` stay, with the app-vs-user distinction stated.
- `README.md` — onboarding flow; the `scripts/gmail_auth.py` instructions are
  removed.
- `docs/architecture-flow.md` — the drafting Mermaid diagram gains the
  profile/résumé inputs.
- `pyproject.toml` — adds `pypdf`.

## Out of scope for Stack 2

Pool browsing, contact selection and the cap, quotas, the token bucket, BYOK, and
scheduling. The Stack 1b drafting bridge still stands in for user selection —
now running per user with that user's own profile and mailbox.
