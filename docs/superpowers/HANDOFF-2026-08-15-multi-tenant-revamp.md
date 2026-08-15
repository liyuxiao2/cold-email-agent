# Handoff — Multi-Tenant Revamp, stopped after Stack 2

_Written 2026-08-15. Stacks 1a, 1b, and 2 are built and in review. Stacks 3 and 4 are specced and planned but **not started**._

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
| **#39–#58** | `mt/01-…` → `mt/20-…` | Stack 1a — auth & roles |
| **#59–#80** | `mt/21-…` → `mt/42-…` | Stack 1b — data model split |
| **#81–#96** | `mt/43-…` → `mt/58-…` | Stack 2 — per-user sender identity |

**58 PRs, at most 7 files each.** Read them in order starting at #39; each PR body names its
stack position and its base branch. Branch names sort in stack order.

⚠️ **Intermediate PRs are not individually green.** A rename cascades across several PRs (e.g.
deleting the `Lead` ORM model breaks ~30 callers until the following PRs land), so the **stack
tip (#96 / `mt/58-docs-handoff`) is the green bar** — 314 tests pass there. This was a deliberate
choice: forcing every PR green would either merge them back together or require throwaway shims.

An earlier attempt shipped this as three subsystem-sized PRs (42 / 63 / 52 files); those were
closed as unreviewable and superseded by this stack. Same commits, same final tree — verified
byte-identical via `git rev-parse <tip>^{tree}`.

**Not started:** Stack 3 (`feat/pool-and-drafting`) and Stack 4 (`feat/scheduling`). Both are
fully specced and planned — see §5. **Build them as deep stacks of ≤7-file PRs from the start**,
using each plan's task boundaries as PR boundaries.

Test count grew 52 → 136 → 256 → 314. `ruff check .`, `ruff format --check .`, and
`cd frontend && npm run build` all pass on `feat/sender-identity`.

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

## 5. What remains: Stacks 3 and 4

Both are fully specced and planned. Read the spec, then the plan.

### Stack 3 — `feat/pool-and-drafting` (base: `feat/sender-identity`)

- Spec: `docs/superpowers/specs/2026-08-14-stack-3-pool-drafting-design.md`
- Plan: `docs/superpowers/plans/2026-08-14-stack-3-pool-drafting.md` (9 tasks)

Ships: pool browser, contact selection with a global per-contact cap, on-demand per-user
drafting, a Redis token bucket replacing `time.sleep`, per-user quota, optional BYOK.

**The most important thing in Stack 3 is a deletion.** `bridge_queue_admin_outreach` in
`cold_email/workers/drafting/drafting.py` is temporary scaffolding from Stack 1b, banner-marked
for removal. **If left in place after Stack 3 lands it would look like working software while
silently:** re-creating admin outreach for every researched company every 15 minutes (undoing a
user's deselection), bypassing the per-contact cap and quotas entirely (it consults neither),
and always choosing the single highest-confidence contact — reintroducing the exact "every user
emails the same founder" problem the whole contact-spreading design exists to prevent.

Also note: Stack 3's plan specifies `drafting_task(user_id)`. Stack 2's merge-gate fix already
made the sweep group by user internally without changing the Celery signature, so Stack 3
narrows it to one user per dispatch — check `drafting.py`'s comment before rewriting.

### Stack 4 — `feat/scheduling` (base: `feat/pool-and-drafting`)

- Spec: `docs/superpowers/specs/2026-08-14-stack-4-scheduling-design.md`
- Plan: `docs/superpowers/plans/2026-08-14-stack-4-scheduling.md` (6 tasks)

Ships: per-email scheduled sends, a daily cadence, a due-send Beat scanner.

Most of the schema already exists — `outreach.scheduled_send_at` was created in 1b and
`pending_sends` already filters on it, so NULL means "send immediately" today.

**The load-bearing property is the claim-before-dispatch.** Celery guarantees at-least-once
*task* delivery, so a scanner running every 5 minutes over rows that only leave the set on
success will eventually dispatch one twice — and a cold email sent twice to a founder cannot be
undone. The scanner must mark rows `sending` in the **same `UPDATE`** that selects them
(`UPDATE ... WHERE status='approved' RETURNING id`) and dispatch only the returned ids. Rows
stuck in `sending` are dead-lettered, **never auto-retried** — retrying a send whose outcome is
unknown is exactly how a double-send happens.

Also: `celery_app.py` sets `timezone="America/Toronto"`, which governs **Beat's cron
interpretation only**. The scanner must use `datetime.now(timezone.utc)` explicitly. Inheriting
that process default is how a scheduler ends up five hours off in production and correct on a
laptop. Both DST transitions have specified tests.

---

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
