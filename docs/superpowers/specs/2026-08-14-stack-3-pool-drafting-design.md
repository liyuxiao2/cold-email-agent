# Stack 3 — Pool Browsing, Contact Selection & Per-User Drafting

_Date: 2026-08-14_
_Branch: `feat/pool-and-drafting` (base: `feat/sender-identity`)_
_Parent spec: [Multi-Tenant Revamp Overview](2026-08-14-multi-tenant-revamp-overview-design.md)_

## Goal

Give users the product: browse the global company pool, pick targets, and get
drafts. This stack replaces the Stack 1b bridge with real user selection, adds
the contact-spreading machinery that keeps the shared pool from spamming
founders, and makes concurrent multi-user LLM access actually work.

## Contact selection

`cold_email/contact_selection.py`:

```python
CONTACT_CAP_DEFAULT = 3  # settings.contact_cap


def select_contact(session, company_id, cap) -> UUID | None:
    """Least-globally-contacted eligible contact under the cap.

    Tie-break: highest Hunter confidence, then is_founder, then id.
    Returns None when every eligible contact is capped.
    """
```

Reads the `available_contacts` view (`contact_id, company_id, confidence,
is_founder, use_count`), filters `use_count < cap`, and orders by
`(use_count ASC, confidence DESC, is_founder DESC, id ASC)`.

`id ASC` as the final tie-break is not decoration: without a total ordering,
Postgres may return either of two equal rows, and the function's tests become
flaky for reasons that look like a selection bug.

### Why least-used and not random

Random distribution is lumpy. With 6 contacts and 6 users, random assignment hits
some address twice and another zero times — the exact outcome the feature exists
to prevent. Least-used spreads evenly *by construction*.

The decisive argument is testability. `select_contact` is a pure function over
counts, so the cap, the ordering, and the exhaustion case are all directly
assertable. A randomised version needs seeding or mocking, and the property you
actually care about — even distribution — becomes a statistical claim rather than
an assertion.

`is_founder DESC` sits *below* `use_count`, so a founder is preferred only among
equally-used contacts. Placing it above `use_count` would re-concentrate volume
on founders, which is what this whole mechanism exists to stop.

### The cap

A contact may appear on at most `cap` outreach rows, ever. `settings.contact_cap`
defaults to 3.

There is a benign race: two concurrent `POST /api/outreach` calls can both read
`use_count = 2` and both insert, pushing a contact to 4. Handling:

- `UNIQUE (user_id, company_id)` already prevents the same *user* double-targeting
  a company, which is the case that actually matters.
- Exceeding the cap by one under concurrency is acceptable. Enforcing it exactly
  needs `SELECT ... FOR UPDATE` on the contact row, serialising pool selection
  across all users for a bound that is itself a heuristic.

This is recorded as a deliberate choice, not an oversight: **the cap is a
spreading heuristic, not an invariant.**

## Pool query

`GET /api/companies` returns companies that:

1. have `research_status = 'researched'`,
2. have at least one eligible contact with `use_count < cap`,
3. have **no** outreach row for the calling user.

Condition 3 is the tenancy-sensitive one — it is a `NOT EXISTS` against
`outreach` scoped to `current_user.id`. A `LEFT JOIN` on `outreach` without the
user predicate would leak the fact that *someone else* is working a company, and
would wrongly hide it from everyone.

| Param | Purpose |
|---|---|
| `industry`, `funding_stage` | Exact match |
| `headcount_min`, `headcount_max` | Range |
| `search` | `ILIKE` on `company_name` |
| `has_founder_contact` | Only companies whose eligible pool includes a founder |
| `limit`, `offset` | Pagination, mirroring the existing `/leads` shape |

Each row includes `contact_count` (eligible, under cap) so the UI can show "3
contacts available" without a second request.

## API surface

| Endpoint | Auth | Behaviour |
|---|---|---|
| `GET /api/companies` | user | The pool, filtered as above |
| `GET /api/companies/{id}` | user | One company + research + eligible contact summaries |
| `POST /api/outreach` | user | `{company_ids: [...]}` → create `queued` rows, dispatch drafting |
| `GET /api/outreach` | user | The caller's outreach rows, filterable by status |
| `GET /api/outreach/drafts` | user | The caller's review queue (`status='drafted'`) |
| `POST /api/outreach/{id}/approve` | user | → `approved`, dispatch send |
| `POST /api/outreach/{id}/reject` | user | → `rejected` with notes |
| `POST /api/outreach/{id}/regenerate` | user | → `queued`, dispatch drafting |
| `GET /api/quota` | user | `{used, limit, period_end}` |
| `GET /api/llm-key` / `PUT` / `DELETE` | user | BYOK management (never returns the key) |
| `POST /api/pipeline/{discovery,research}` | **admin** | unchanged |

`GET /api/companies/{id}` returns contact *summaries* — first name, position,
whether it is the founder — but **never the email addresses**. The pool is the
product's inventory; exposing a scrapeable list of verified founder emails to
every signed-up user turns the app into a lead-list leak. The address is revealed
only in the user's own draft, after a contact has been assigned to them.

### `POST /api/outreach` semantics

Partial success, not all-or-nothing:

```json
{
  "created":  [{"outreach_id": "...", "company_id": "...", "contact_id": "..."}],
  "skipped":  [{"company_id": "...", "reason": "no_available_contact"}],
  "quota":    {"used": 12, "limit": 100}
}
```

Skip reasons: `no_available_contact`, `already_targeted`, `not_researched`,
`quota_exceeded`. A user selecting 20 companies where 2 became exhausted between
page load and submit should get 18 drafts and a clear note — not a 400 and an
empty result.

One `drafting_task.delay(user_id)` is dispatched after the batch, regardless of
how many rows were created. The task sweeps all of that user's `queued` rows, so
per-company dispatch would just be redundant work.

## The Redis token bucket

`cold_email/workers/shared/rate_limit.py` replaces
`time.sleep(LLM_MIN_INTERVAL_SECONDS)`.

`★ Why this is not a style change:` a `sleep` paces **one worker process**. The
actual constraint is a provider quota shared by every worker, every user, and
every task type. With one user the two were indistinguishable; with N users
drafting concurrently, `sleep` guarantees 429s while each process politely waits
its own turn.

```python
def acquire(key: str, rate: float, burst: int, timeout: float) -> bool:
    """Block until a token is available or `timeout` elapses."""
```

A Lua script does the check-and-decrement atomically — a read-then-write in
Python has a race between processes that defeats the purpose. Keyed per model
(`llm:gemini-3.5-flash-lite`), since each model in `model_fallback_chain` has its
own quota. Redis is already in the stack as the Celery broker.

Interaction with the fallback chain: a token acquisition that times out is
treated exactly like a `429` from that model — `generate_json` skips to the next
model in the chain. That reuses the existing skip logic rather than adding a
second, parallel notion of "this model is unavailable."

`LLM_MIN_INTERVAL_SECONDS` and the `rate_limit=LLM_RATE_LIMIT` decorator argument
on `research_task` are removed. Celery's `rate_limit` is per-worker, so it has the
same defect as `sleep`.

## Quota

`cold_email/quota.py`:

```python
def usage(session, user_id) -> tuple[int, int]     # (used_this_period, limit)
def check(session, user_id, requested: int) -> int  # how many are allowed
```

"Used" counts `outreach` rows created by the user since the start of the current
calendar month, UTC. Counting *creations* rather than *sends* is deliberate: the
LLM call is the cost, and it happens at drafting. A user who drafts 100 and
approves 3 has spent 100 units of the thing being rationed.

`users.monthly_draft_quota INT NOT NULL DEFAULT 100`. Per-user rather than
global so it can be raised for individuals without a deploy — and it is the seam
Stripe plans will attach to later.

BYOK users bypass the quota (they are spending their own limits) and bypass the
token bucket (their limits are not shared).

## BYOK

```sql
-- migrations/008_user_llm_and_quota.sql
ALTER TABLE users ADD COLUMN llm_api_key_enc     BYTEA;
ALTER TABLE users ADD COLUMN llm_provider        TEXT;    -- groq | gemini
ALTER TABLE users ADD COLUMN monthly_draft_quota INT NOT NULL DEFAULT 100;
```

`resolve_llm_credentials(user) -> LlmCredentials` in `workers/shared/llm.py`:

| Case | Key | Bucket | Quota |
|---|---|---|---|
| User has a key | theirs (Fernet-decrypted) | bypassed | bypassed |
| No key | platform's | enforced | enforced |
| Self-hosted (no users, env keys) | env | enforced (harmless) | n/a |

`PUT /api/llm-key` validates the key with one trivial live call before storing
it. Storing an invalid key means the user's next 40 drafts fail one at a time in a
Celery worker, and they see a DLQ full of auth errors instead of a form
validation message.

The key is **never** returned by any endpoint. `GET /api/llm-key` returns
`{provider, configured: true, last4}`.

## Worker changes

`drafting_task(user_id)`:

- Signature changes from a no-arg global sweep to per-user.
- **The Stack 1b bridge is deleted.** The comment naming Stack 3 as its removal
  point is the marker.
- Sweeps that user's `queued` outreach rows from `pending_drafts`.
- Loads profile, résumé bytes, and Gmail credentials once (Stack 2).
- Acquires a token before each LLM call.

Beat schedule: the every-15-minutes global sweep becomes
`drafting-recovery-sweep` — hourly, iterating users who have `queued` rows older
than 30 minutes. This is a safety net for a dropped dispatch, not the primary
path. Without it, a Redis hiccup during `POST /api/outreach` leaves rows `queued`
forever with no user-visible explanation.

`POST /api/pipeline/drafting` becomes admin-only and triggers the recovery sweep
across all users.

## Frontend

```
app/pool/page.tsx              # browse + multi-select + "Draft these (N)"
components/CompanyPool.tsx     # filters, pagination, selection state
components/CompanyCard.tsx     # research hook, contact_count, tech stack
components/QuotaBar.tsx        # used/limit, shown in the pool header
components/LlmKeyForm.tsx      # BYOK, on the profile page
```

The review deck (Stack 1b) already shows which contact is being emailed. The pool
shows `contact_count` but no addresses, matching the API.

`QuotaBar` renders before selection, not after submission. A user who selects 60
companies against a remaining quota of 12 should learn that while choosing.

## Error handling

| Condition | Response |
|---|---|
| `POST /api/outreach` over quota | `200` with the allowed subset created and the rest `skipped: quota_exceeded` |
| All selected companies exhausted | `200`, `created: []`, every entry in `skipped` |
| Company not `researched` | `skipped: not_researched` |
| Outreach row belongs to another user | `404`, **not** `403` — a 403 confirms the id exists |
| `PUT /api/llm-key` with an invalid key | `422`, nothing stored |
| Token bucket timeout | model skipped in the chain; sweep continues |
| Redis unreachable | rate limiting **fails open** with a warning |

Two of these deserve their reasoning stated:

**`404` for another user's outreach id.** Returning `403` tells an attacker the
id is real, turning an authorization check into an existence oracle. Every
user-scoped lookup filters by `user_id` in the query and 404s on no rows, so the
correct response falls out of the query shape rather than depending on a
remembered convention.

**Redis failing open.** A rate limiter that fails *closed* converts a Redis blip
into a total drafting outage. Failing open risks a burst of provider 429s, which
the existing fallback chain already handles gracefully. The cheaper failure wins.

## Testing

`tests/test_contact_selection.py` — pure, no network:

- Picks the least-used contact among several.
- Confidence breaks a `use_count` tie; `is_founder` breaks a confidence tie.
- Every eligible contact at cap → `None`.
- Ineligible contacts are never selected, even at `use_count = 0`.
- Sequential selections spread across contacts round-robin rather than repeating.
- `use_count` counts outreach rows across **all** users, not just the caller.

`tests/test_rate_limit.py`

- `burst` tokens acquire immediately; the next blocks.
- Tokens refill at `rate`.
- Timeout returns `False` rather than blocking forever.
- Concurrent acquirers never jointly exceed the bucket (the atomicity guarantee).
- Redis down → returns `True` and logs (fails open).

`tests/test_quota.py`

- Counts only the caller's rows, only the current period.
- `check` clamps a request to the remaining allowance.
- A BYOK user bypasses the limit.

`tests/test_pool_api.py`

- Companies with no eligible contact are absent from the pool.
- A company the caller already has outreach for is absent — but **still present
  for a different user**. This is the single most important test in the stack: it
  is the difference between a shared pool and a broken one.
- Exhausted companies are absent for everyone.
- `GET /api/companies/{id}` response contains **no** email addresses.
- Non-`researched` companies never appear.

`tests/test_outreach_api.py`

- Partial success: 3 selected, 1 exhausted → 2 created, 1 skipped.
- Re-selecting a targeted company → `already_targeted`, no duplicate row
  (`UNIQUE(user_id, company_id)` holds).
- Approve/reject/regenerate on another user's id → 404, no mutation.
- Approve dispatches logistics for the **owning** user's credentials.

## Documentation updated in this stack

- `CLAUDE.md` — the drafting pipeline section rewritten for on-demand per-user
  drafting; new pool/outreach/quota/BYOK endpoints in the table; the Beat schedule
  section updated (recovery sweep, not primary path); a Rate Limiting & Quota
  section replacing the "Calls paced under the free-tier limit" note; `CONTACT_CAP`
  and quota env vars added.
- `docs/architecture-flow.md` — the Mermaid pipeline diagram gains the
  user-selection entry point and the token bucket.
- `README.md` — the user-facing flow: browse → select → review → approve.

## Out of scope for Stack 3

Scheduling (Stack 4): `POST /approve` sends immediately, since
`scheduled_send_at` stays NULL and `pending_sends` already treats NULL as "send
now". Also out: Stripe, teams, reply tracking.
