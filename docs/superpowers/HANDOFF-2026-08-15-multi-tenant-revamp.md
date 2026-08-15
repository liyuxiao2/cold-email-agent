# Handoff — Multi-Tenant Revamp (all four phases complete)

_Written 2026-08-15, updated after completion. **All four phases are built and in review as 108 stacked PRs.** 450 tests pass at the stack tip._

---

## 1. What this project is becoming

`cold-email-agent` was a single-tenant tool: one person's identity was compiled into the
codebase (`sender_profile.PROFILE`, `resume.txt`/`resume.pdf` in the repo, one global Gmail
refresh token), and every lead in the database implicitly belonged to them.

It is being converted into a commercial multi-user product:

- Users log in with Google.
- Each user attaches their own résumé and sends from their own Gmail.
- Companies and their research live in **one global pool**, discovered and researched once by
  an admin, reused by everyone.
- Non-admins **cannot** trigger discovery or research. They browse the pool, draft, approve,
  and send/schedule.

Billing was explicitly deferred. Nothing in the design blocks Stripe later.

---

## 2. Current state

### Open PRs — one deep linear stack, each PR based on the previous

| PRs | Branches | Contents |
|---|---|---|
| [#35](https://github.com/liyuxiao2/cold-email-agent/pull/35) | `docs/multi-tenant-revamp-specs` | Specs + plans only, no code |
| **#39-#58** | `mt/01-…` → `mt/20-…` | Phase 1a — auth & roles |
| **#59-#80** | `mt/21-…` → `mt/42-…` | Phase 1b — data model split |
| **#81-#96** | `mt/43-…` → `mt/58-…` | Phase 2 — per-user sender identity |
| **#100-#127** | `mt/59-…` → `mt/86-…` | Phase 3 — pool, contact spreading, per-user drafting, quota, BYOK |
| **#128-#149** | `mt/87-…` → `mt/108-…` | Phase 4 — scheduled sends + daily cadence |

**108 code PRs, at most 7 files each.** Read them in order; each PR title carries its stack
position (`[N/108]`) and each body names its base branch. Branch names sort in stack order.
The PR *numbers* above are GitHub's and are not contiguous with the stack positions — trust the
titles.

**Everything is built.** Tests grew 52 → 136 → 256 → 314 → 384 → **450**. `ruff check .`,
`ruff format --check .`, and `cd frontend && npm run build` all pass at the stack tip
(`mt/108-feat-frontend-approve-all-control-fix-mi`).

⚠️ **Intermediate PRs are not individually green.** A rename cascades across several PRs (deleting
the `Lead` ORM model breaks ~30 callers until the following PRs land), so the **stack tip is the
green bar.** This was deliberate: forcing every PR green would either merge them back together or
require throwaway shims.

An earlier attempt shipped phases 1a-2 as three subsystem-sized PRs (42 / 63 / 52 files); those
were closed as unreviewable and superseded by this stack. Same commits, same final tree — verified
byte-identical via `git rev-parse <tip>^{tree}`.

### Where to read the design

| Document | Purpose |
|---|---|
| `docs/superpowers/specs/2026-08-14-multi-tenant-revamp-overview-design.md` | **Start here.** Every decision and its rationale. |
| `docs/superpowers/specs/2026-08-14-stack-{1a,1b,2,3,4}-*-design.md` | Per-stack designs |
| `docs/superpowers/plans/2026-08-14-stack-{1a,1b,2,3,4}-*.md` | Per-stack TDD implementation plans |
| `.superpowers/sdd/2026-08-14-stack-*/progress.md` | **Execution ledgers — every ruling made during the build.** Gitignored, so local-only. |

⚠️ The ledgers in `.superpowers/sdd/` are **gitignored and local to that machine.** If you are
a fresh session on a different machine, they are gone — this handoff and the PR bodies are the
durable record. Everything load-bearing from them is reproduced below.

---

## 3. Things you must know before touching this code

These are the non-obvious properties. Several were discovered the hard way.

### Production provisions its schema with `create_all`, NOT by running migrations

`scripts/start.sh` runs `Base.metadata.create_all(sync_engine)`. That is how production
actually gets its tables. Consequences that have already bitten:

- **Indexes declared only in SQL never existed in production.** Fixed: the ORM now declares
  all of them, including the partial `company_contacts_eligible_idx ... WHERE eligible`, and
  `tests/test_migration.py::test_create_all_and_the_migration_agree_on_indexes_and_constraints`
  diffs both provisioning paths and fails on drift. **Extend that test's migration list every
  time you add a migration.**
- **`create_all` does not model views.** `pending_drafts`, `pending_sends`, and
  `available_contacts` did not exist in production at all. Fixed: `scripts/apply_views.py`
  applies `migrations/views.sql` on every boot.
- **`create_all` cannot express `SET STORAGE EXTERNAL`.** Fixed: `scripts/apply_storage.py`
  applies `migrations/storage.sql` on every boot. It reads a `SQL_FILES` tuple —
  **append to that rather than inventing a new mechanism.**
- Column-level drift remains and is deferred: `created_at`/`updated_at` are `NOT NULL` on the
  migration path but nullable via `create_all`, and ~10 server defaults exist only in SQL. ORM
  inserts are unaffected; hand-written SQL would diverge.

### Migration 006 must be run by hand, before deploying Stack 1b

Nothing in the repo invokes it. Skipping it produces the worst failure shape: `create_all`
makes the new tables **empty**, the stale views survive, `/api/health` returns **200** so Cloud
Run cuts traffic over, and the drafting sweep then raises
`TypeError: PendingDraft() got an unexpected keyword argument 'lead_id'` every 15 minutes while
every lead sits stranded in `leads`. It boots green and silently does nothing.

```bash
# pre-check: does the drafts-orphan guard fire?
SELECT l.status, count(*) FROM drafts d JOIN leads l ON l.id = d.lead_id
WHERE l.status NOT IN ('drafted','approved','sent','rejected')
  AND NOT (l.status='failed' AND l.founder_email IS NOT NULL)
GROUP BY 1;

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/006_multi_tenant_schema.sql
```

`leads` becomes `leads_legacy` and is **never dropped** — a bad run is recoverable without a
backup restore. A follow-up PR should drop it once proven.

⚠️ **One recommended one-liner before you run it.** The review flagged a *plausible* (not
confirmed) edge: the outreach backfill selects leads by draft-existence independent of
`founder_email`, so a lead with drafts but no email would get an `outreach` row with
`contact_id = NULL` — visible in the deck but not re-draftable. The app's own invariant
precludes it, **but this migration already had one "assumed by construction" invariant turn out
false**, so add `AND l.founder_email IS NOT NULL` to that `OR EXISTS` clause rather than trust a
second one.

### The Gmail OAuth split is the easiest thing to get backwards

- **App-level, in `settings`:** `gmail_client_id`, `gmail_client_secret`. Google requires them
  to refresh **any** user's token.
- **User-level, on the `users` row:** `gmail_refresh_token_enc` (Fernet), `gmail_sender_email`.

Moving all four to the users table is the classic multi-tenant mistake — nothing can then be
refreshed. There is a regression test asserting `create_draft` reads credentials from its
*argument*, never from settings.

### `ENCRYPTION_KEY` is unrecoverable

Losing or rotating it makes every stored Gmail refresh token undecryptable and forces every
user to re-consent. It must be generated once and backed up **before** anyone signs in.

### Signups are default-deny

Empty `ALLOWED_SIGNUP_EMAILS` + `ALLOWED_SIGNUP_DOMAIN` means **only `ADMIN_EMAIL` can sign
in.** This exists because a `role='user'` account can read every lead — including every scraped
founder email — and call `POST /api/outreach/{id}/approve`, which sends a real cold email
through the sender's mailbox. Widen deliberately.

### Test environment

- Postgres + Redis run via docker compose (`cold-email-agent-postgres-1`,
  `cold-email-agent-redis-1`). Apply SQL with `docker exec`, not host `psql`.
- Test DB is `cold_email_test`; dev DB is `cold_email`. **Never touch `cold_email` from tests.**
  If you run raw SQL against the test DB, leave its schema clean — stray objects break the
  suite. (I did this to myself and had to `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`
  plus re-`GRANT` to recover.)
- Fixtures in `tests/conftest.py`: `async_session`, `client` (anonymous),
  `user_client` (user@example.com, role=user), `admin_client` (admin@example.com, role=admin),
  `admin_user_id`, `sync_session_for`, `pending_views`, plus migration-specific legacy-schema
  fixtures. The `*_client` fixtures mint **real** session cookies, so gating tests exercise the
  genuine verify → DB lookup → role check chain.
- **Worker tests need `sync_session_for`. Read its docstring first.** Workers use a sync
  session; tests use async. Proxying `async_session.sync_session` raises `MissingGreenlet` —
  do not retry that. The working bridge is a real `sqlalchemy.orm.Session` on its own sync
  engine against the same physical database. Pattern: arrange via `async_session` and
  `await async_session.commit()`, call the worker function directly, then assert via
  `async_session` with `refresh()` **after** it returns.

### Local environment quirks

- **Commit signing is broken and commits are currently unsigned.** `commit.gpgsign=true` with
  `gpg.ssh.program=op-ssh-sign` (1Password). The app auto-locked mid-session; a plain
  `git commit` **hangs 60+ seconds then fails**. Commits `4ffe9f0`–`b028e7b`… through `51a22b5`
  are signed; everything from `263d452` onward is not, because the user chose to continue
  unsigned. **This will fail any branch-protection rule requiring signed commits.** Either
  unlock 1Password and re-sign, or accept it.
- The corporate npm mirror enforces `min-release-age=5`: `npm ci` **fails** locally on recent
  tarballs. `npm run build` works. Do not run `npm ci`.
- The Python package mirror 403s `pypdf==6.16.1` specifically, hence `!=6.16.1` in
  `pyproject.toml`.
- `uv.lock` is gitignored in this repo.

---

## 4. Bugs found and fixed along the way

Worth knowing, because several were latent for a long time and the same classes may recur.

| Bug | Why it was invisible |
|---|---|
| `cors_origins = ["*"]` with `allow_credentials=True` — **browsers reject that outright** | No credentialed request existed until auth |
| CI set no `SESSION_SECRET`/`ENCRYPTION_KEY`, so auth tests would fail in CI while passing locally | Those tests didn't exist yet |
| CI `pull_request` was filtered to `[main]`, so **none of these stacked PRs would have gotten CI** | No stacked PRs existed before |
| CI ran `ruff check --fix` / `ruff format` without `--check` — silently auto-fixing a throwaway checkout instead of failing | Never drifted enough to notice |
| `TEST_DB_URL`'s `.replace("/cold_email", ...)` also matched the **username** in the connection string | No test had opened a real connection through that fixture |
| `research`'s FK to `leads` survived the table rename, pointing at `leads_legacy` and blocking every future research insert | Only reachable during the one-time migration |
| `"cto"` is a substring of "dire**cto**r", `"coo"` of "**coo**rdinator" — Creative Director and Office Coordinator classified as eligible decision-makers | Substring matching looked obviously fine |
| Reviewer notes written to `leads.error_msg` were backfilled into the **global** `companies.error_msg`, which `/api/companies` renders to every user | The column name gave no hint it held per-user text |
| `CREATE OR REPLACE VIEW` **cannot rename a view column**, so `views.sql` failed wholesale against production's `pending_drafts` — and `apply_views.py` runs the file as one string, so all three views were skipped | "Idempotent" was assumed, not tested against the actual transition |
| `drafting_task` read `pending_drafts` unfiltered and applied **one** user's profile, résumé and mailbox to **every** user's rows | Unreachable while only the single-admin bridge created outreach rows |
| `pypdf` parsing + the LLM call ran directly in an `async def` route, stalling `/api/health` | Only endpoint doing blocking work |
| Extracted résumé text had only a lower bound → a dense PDF poisons that user's drafting forever, retried every 15 min | The "leave rows queued" recovery design is what made it infinite |
| The shared résumé-extraction prompt contained a **real person's name and employers** — in the branch whose goal was removing hardcoded identities | It read as helpful few-shot guidance |

---

## 5. Phases 3 and 4, as built

Both landed. What matters if you touch them:

### Phase 3 — pool browsing, contact spreading, per-user drafting (PRs #59-#86)

**The most important thing in it was a deletion.** `bridge_queue_admin_outreach` — temporary
scaffolding from phase 1b — is gone. Had it survived, it would have looked like working software
while re-creating admin outreach for every researched company every 15 minutes: silently undoing a
user's deselection, bypassing the per-contact cap and quotas entirely (it consulted neither), and
always choosing the single highest-confidence contact, reintroducing the exact "every user emails
the same founder" problem the phase existed to eliminate.

**Contact spreading works as designed** — verified by running the real function against the live
view. `select_contact` orders `use_count ASC, confidence DESC, is_founder DESC, contact_id ASC`.
`is_founder` deliberately sits BELOW `use_count`: above it, volume re-concentrates on exactly the
address spreading protects. The final `contact_id ASC` gives a total ordering, without which two
equal rows make tests flaky in a way that looks like a selection bug. The cap is a **heuristic,
not an invariant** — concurrent requests can exceed it, and enforcing it exactly would serialise
pool selection across all users for a bound that is itself approximate.

**Two Criticals were caught by the final review, not by tests:**
- **BYOK was 100% broken for Gemini.** `generate_json` walked the model chain without consulting
  `credentials.provider`, so a correctly-labelled Gemini key went to Groq first; Groq's
  `AuthenticationError` isn't fallback-able, so it re-raised, was swallowed as a transient, and the
  hourly sweep re-dispatched the same doomed batch forever. The user saw "Key saved" and their
  selections silently never produced drafts.
- **Migration 008 was applied by nothing.** It was the first migration to ALTER an existing table,
  and `create_all` never alters. Boot: `seed_admin` failed and was swallowed, `/api/health`
  returned 200 (it only counts `Company`), Cloud Run cut traffic over, and then every
  authenticated request 500'd. A full outage the platform reported as healthy.

### Phase 4 — scheduled sends and cadence (PRs #87-#108)

**The send path is the most consequential code in the project**, because sending twice is
unrecoverable. Three layers protect it:

1. The scanner claims rows in the **same `UPDATE` that selects them**
   (`WHERE status='approved' ... RETURNING id`) and dispatches only the returned ids. Celery
   guarantees at-least-once *task* delivery, which over a set that only empties on success becomes
   at-least-once *email*.
2. `logistics_task` re-checks the row is still `sending` before sending.
3. **A second claim, immediately before the send: `gmail_draft_id` itself is the ticket.**
   `claim_send_ticket` nulls it via `UPDATE ... WHERE gmail_draft_id = :expected RETURNING id`, so
   two concurrent executions of the same claimed row cannot both send. Everything from that claim
   onward is handled inline and never re-raised, so a post-send failure can't trigger Celery's
   autoretry into a second send.

`sent` is marked **after** the send, deliberately: a lost email that looks delivered is invisible
forever, whereas a delivered email that looks pending lands in the DLQ where a human will look.

**The reaper policy is deliberately inverted from the drafting one.** A stranded `drafting` row is
reclaimed and retried (bounded, then dead-lettered) because no email has left the building — the
Gmail draft is only created after the LLM call succeeds. A stranded `sending` row is
**dead-lettered and never auto-retried**, because the send may already have gone out. Same claim
shape, opposite policy. Don't let "follow the existing pattern" collapse them.

**Time handling:** everything is stored and compared in UTC; the cadence carries an IANA zone
*name*. Storing local times makes DST a correctness bug (`America/Toronto` has a day with no 02:30
and a day with two), and a fixed offset is wrong for half the year. `celery_app.timezone` governs
Beat's cron interpretation ONLY — the scanner uses `datetime.now(timezone.utc)` explicitly.

## 6. How this was built, if you want to continue the same way

Execution used `superpowers:subagent-driven-development`: a fresh implementer subagent per task
(or per small batch of same-shaped tasks), a spec-compliance + quality review after each, and a
whole-branch review on the most capable model at the end of each stack, followed by exactly one
fix wave.

That final whole-branch review is where the value was. It found the cross-tenant email bug, the
reviewer-notes leak, the substring-matching bug, and the "boots green and does nothing" deploy
path — none of which any per-task review could see, because each was correct in isolation and
wrong across a seam.

Ledger discipline matters: `.superpowers/sdd/<plan>/progress.md` records every ruling. Without
it, a compacted session re-dispatches completed work.

⚠️ Stack 2's merge-gate fix commit (`2257397`) did **not** get an independent scoped re-review —
it was verified by direct inspection plus the full suite, which is weaker.

---

## 7. Immediate suggested next steps

1. **Review the three code PRs bottom-up** (#36 → #37 → #38). They're designed to be read in
   order and each PR body leads with what's risky.
2. **Decide on commit signing** before merging anything (§3).
3. **If merging Stack 1b:** run the pre-check query, add the `AND l.founder_email IS NOT NULL`
   conjunct, then run migration 006 by hand. Do not deploy without it.
4. **Then Stack 3**, whose first job is deleting the drafting bridge.
