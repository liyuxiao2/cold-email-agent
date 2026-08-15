# Stack 4 — Scheduled Sends & Daily Cadence

_Date: 2026-08-14_
_Branch: `feat/scheduling` (base: `feat/pool-and-drafting`)_
_Parent spec: [Multi-Tenant Revamp Overview](2026-08-14-multi-tenant-revamp-overview-design.md)_

## Goal

Let a user approve a draft now and have it delivered later — either at a datetime
they pick, or spread automatically by a daily cadence. Cadence is the
deliverability feature: forty emails leaving one Gmail account in ninety seconds
is the clearest spam signal a new sender can produce.

Most of the schema already exists. `outreach.scheduled_send_at` was created in
Stack 1b and `pending_sends` already filters on it, so this stack adds one column
to `users`, one Beat task, and the slot arithmetic.

## Data model

```sql
-- migrations/009_send_cadence.sql
ALTER TABLE users ADD COLUMN send_cadence JSONB;

CREATE INDEX outreach_due_idx ON outreach (scheduled_send_at)
    WHERE status = 'approved';
```

```jsonc
// send_cadence — NULL means "send immediately on approve"
{
  "max_per_day":  10,
  "days":         [0,1,2,3,4],      // Mon-Fri, Python weekday()
  "window_start": "09:00",
  "window_end":   "17:00",
  "timezone":     "America/Toronto" // IANA
}
```

JSONB rather than five columns: the object is always read and written whole, never
queried into, and it will grow (`min_gap_minutes`, per-day overrides) without a
migration each time.

The **partial index** matters. The due-send scanner runs every five minutes
forever, and `WHERE status = 'approved'` excludes the `sent` rows that will
eventually dominate the table. Indexing all statuses would mean the index grows
without bound while only a sliver is ever read.

## Slot arithmetic

`cold_email/cadence.py`:

```python
def next_slot(cadence: dict, scheduled: list[datetime], now: datetime) -> datetime:
    """Earliest UTC instant satisfying the cadence, given what's already queued.

    Walks forward day by day from `now` in the user's timezone:
      - skip days not in cadence["days"]
      - count `scheduled` already landing on that local day
      - if count >= max_per_day, advance
      - otherwise place the send inside the window, evenly spaced
    Returns UTC. Raises CadenceUnsatisfiable past a 90-day horizon.
    """
```

### Time handling

**All timestamps are stored and compared in UTC.** The cadence carries an IANA
timezone name, used only to answer "which local day is this, and is it inside the
user's window."

Storing local times would make DST a correctness bug: `America/Toronto` has a day
with no 02:30 and a day with two, so a stored local timestamp is either
non-existent or ambiguous twice a year. UTC plus a zone name is unambiguous
always.

The zone name is stored, not a fixed offset, for the same reason: `-05:00` is
wrong for half the year.

⚠️ `celery_app.py` sets `timezone="America/Toronto"` with `enable_utc=True`. That
governs *Beat's cron interpretation*, not the scanner's comparisons. The scanner
must use `datetime.now(timezone.utc)` explicitly and never rely on the Celery
setting — inheriting a process-level default here is how a scheduler ends up five
hours off in production and correct on a laptop.

**Even spacing inside the window**, not all at `window_start`: ten emails at
exactly 09:00:00 is the burst the cadence exists to prevent. With a 09:00–17:00
window and `max_per_day: 10`, slots land every 48 minutes.

`CadenceUnsatisfiable` past 90 days catches the pathological configuration
(`max_per_day: 1`, `days: [6]`) plus a large approved backlog. Failing loudly beats
silently scheduling a send for 2028.

## The due-send scanner

```python
@shared_task(name="cold_email.workers.logistics.send_due_task")
def send_due_task() -> dict:
    """Dispatch logistics_task for every approved outreach row now due."""
```

Reads `pending_sends` — which already carries
`scheduled_send_at IS NULL OR scheduled_send_at <= now()` from Stack 1b — and
dispatches `logistics_task(outreach_id)` per row. Beat: every 5 minutes.

### Double-send prevention

This is the risk that matters. A cold email sent twice to a founder is
unrecoverable, and a scanner running every five minutes over a set that only
leaves on success is exactly the shape that double-sends.

Two guards:

1. **The scanner claims rows before dispatching.** A single
   `UPDATE outreach SET status='sending' WHERE id = ANY(...) AND status='approved'
   RETURNING id` — only ids actually returned get dispatched. Two overlapping
   scanner runs cannot both claim the same row, because the second `UPDATE` matches
   nothing.
2. **`logistics_task` re-checks** that the row is `sending` before calling
   `send_draft`, and sets `sent` after.

`sending` is a new transient `outreach.status`. It is the one place a derived
state is worth adding: `approved` cannot express "already handed to a worker," and
without that distinction, at-least-once delivery from Celery becomes
at-least-once *email*.

A row stuck in `sending` (worker crashed mid-flight) is surfaced, not
auto-retried: after 30 minutes it is dead-lettered at stage `logistics` with
"send status unknown — verify before retrying." Automatically retrying a send
whose outcome is unknown is precisely how a double-send happens.

## Approve semantics

`POST /api/outreach/{id}/approve` gains an optional body:

```jsonc
{ "scheduled_send_at": "2026-08-20T14:00:00Z" }   // all fields optional
{ "send_now": true }                              // override cadence
```

| Body | `scheduled_send_at` | Result |
|---|---|---|
| `scheduled_send_at` given | that instant | delivered at that time |
| `send_now: true` | `NULL` | next scanner tick (≤5 min) |
| empty, cadence set | `next_slot(...)` | next free cadence slot |
| empty, no cadence | `NULL` | next scanner tick |

An explicit past `scheduled_send_at` is accepted and sends on the next tick —
"send this immediately" is a reasonable reading, and rejecting a timestamp that
went stale while the user was reading the draft would be hostile.

`POST /api/outreach/bulk-approve {outreach_ids: [...]}` applies the cadence
across the whole batch in one `next_slot` walk, so approving thirty drafts
produces thirty spread slots rather than thirty identical ones. Without this,
cadence is unusable at the volume that makes cadence necessary.

## Cadence management

| Endpoint | Auth | Behaviour |
|---|---|---|
| `GET /api/cadence` | user | Current cadence, or `null` |
| `PUT /api/cadence` | user | Validate and save |
| `DELETE /api/cadence` | user | Revert to send-immediately |
| `GET /api/outreach/scheduled` | user | Upcoming sends, ascending |
| `POST /api/outreach/{id}/unschedule` | user | → `drafted`, clears the timestamp |

Validation on `PUT`: `1 <= max_per_day <= 50`; `days` a non-empty subset of 0–6;
`window_end > window_start`; `timezone` resolvable by `zoneinfo`. An unresolvable
zone name must 422 — accepting it would make `next_slot` raise inside a Celery
worker, turning a form typo into a background failure the user cannot connect to
their action.

The 50/day ceiling is a deliverability guardrail, not a licence: consumer Gmail
caps around 500/day and a new sender should sit far below it.

`POST /unschedule` returns a row to `drafted`, not `queued` — the draft already
exists and re-running the LLM would waste quota and produce different copy than
the user reviewed.

## Frontend

```
components/ScheduleDialog.tsx    # datetime picker + "send now"; shown on approve
components/CadenceSettings.tsx  # profile page: per-day cap, days, window, tz
components/ScheduledQueue.tsx   # upcoming sends timeline, with unschedule
```

The approve button becomes a split control: **Approve** (uses cadence, or sends
now if none) and **Approve & schedule…** (opens the dialog). Default behaviour is
one click; scheduling is opt-in per email.

`CadenceSettings` shows a computed preview — "next 5 sends: Mon 9:00, Mon 9:48,
…" — because a cadence's real behaviour is not obvious from four fields, and a
misconfigured window silently delays a user's whole queue.

Timezone defaults from `Intl.DateTimeFormat().resolvedOptions().timeZone`, with
all times displayed in the user's cadence timezone and a UTC offset shown
alongside.

## Error handling

| Condition | Response |
|---|---|
| Invalid cadence field | `422`, nothing saved |
| Unresolvable IANA timezone | `422` |
| `scheduled_send_at` in the past | accepted; sends next tick |
| `scheduled_send_at` > 90 days out | `422` |
| `CadenceUnsatisfiable` during approve | `409` "cadence cannot fit this send within 90 days" |
| Approve on another user's outreach | `404` |
| Row in `sending` > 30 min | dead-lettered, `logistics`, "send status unknown" |
| Gmail draft deleted from the mailbox | terminal failure on that row; the rest of the scan continues |

The deleted-draft case is real: `gmail_draft_id` points at a resource the user can
delete by hand in Gmail between approving and the scheduled send. It must be a
per-row terminal failure, never an exception that aborts the scan.

## Testing

`tests/test_cadence.py` — pure functions, the highest-value tests here:

- Empty queue → first slot is the next valid window opening.
- `max_per_day` reached → the next slot lands on the following valid day.
- Non-cadence days are skipped.
- Slots inside a day are evenly spaced across the window, not stacked at the start.
- **DST spring-forward:** a window spanning the skipped local hour still yields
  valid, ordered, strictly-increasing UTC instants.
- **DST fall-back:** the duplicated local hour produces no duplicate UTC slots.
- A non-UTC cadence timezone returns UTC instants that convert back to the
  intended local times.
- `max_per_day: 1, days: [6]` with 200 approved → `CadenceUnsatisfiable`.
- Slots are strictly increasing across a multi-day walk.

`tests/test_send_due.py`

- Rows with `scheduled_send_at` NULL or past are dispatched; future rows are not.
- **Two overlapping scans dispatch each row exactly once** (the claim guarantee).
- A row already `sending` is not re-dispatched.
- A row `sending` for >30 min is dead-lettered, not retried.
- `logistics_task` on a row no longer `sending` is a no-op.
- Only the owning user's Gmail credentials are used.
- A deleted Gmail draft fails that row alone; the rest of the scan proceeds.

`tests/test_scheduling_api.py`

- Each approve-body variant produces the right `scheduled_send_at`.
- `bulk-approve` with a cadence spreads slots; without one, all NULL.
- Cadence validation rejects each invalid field.
- Unschedule → `drafted`, timestamp cleared.
- **Tenancy:** `GET /api/outreach/scheduled` returns only the caller's rows;
  approve/unschedule on another user's id → 404.

## Documentation updated in this stack

- `CLAUDE.md` — a Scheduling & Cadence section; the logistics pipeline entry
  rewritten (scanner-driven, not approve-driven); `send_due_task` added to the Beat
  schedule; the `sending` status added to the status vocabulary everywhere it is
  listed; new endpoints in the table.
- `docs/architecture-flow.md` — the Mermaid lifecycle diagram gains
  `approved → sending → sent` and the scanner.
- `README.md` — cadence as a user-facing feature.

## Out of scope for Stack 4

Stripe; reply and bounce tracking; follow-up sequences (this schedules one email
per outreach row, never a chain); send-time optimisation by recipient timezone;
per-company overrides. `send_cadence` is JSONB precisely so those become additive.
