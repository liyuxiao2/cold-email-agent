"""Daily send-cadence slot arithmetic.

Cadence is the deliverability feature: forty emails leaving one Gmail account in
ninety seconds is the clearest spam signal a new sender can produce.

TIME HANDLING — the part that is easy to get wrong:

All timestamps are stored and compared in UTC. The cadence carries an IANA
timezone NAME, used only to answer "which local day is this, and is it inside the
user's window."

Storing local times would make DST a correctness bug: America/Toronto has a day
with no 02:30 and a day with two, so a stored local timestamp is either
non-existent or ambiguous twice a year. UTC plus a zone name is unambiguous
always. And the name is stored rather than a fixed offset because -05:00 is wrong
for half the year.
"""

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

HORIZON_DAYS = 90
MAX_PER_DAY_CEILING = 50  # deliverability guardrail; consumer Gmail caps ~500/day


class CadenceInvalid(ValueError):
    """The cadence object is malformed."""


class CadenceUnsatisfiable(RuntimeError):
    """No slot exists within the horizon for this cadence and backlog."""


def _parse_hhmm(value: str, field: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise CadenceInvalid(f"{field} must be 'HH:MM', got {value!r}") from exc


def validate_cadence(cadence: dict) -> dict:
    """Validate and normalise a cadence, or raise CadenceInvalid.

    The timezone check matters most: accepting an unresolvable name would make
    next_slot raise inside a Celery worker, turning a form typo into a background
    failure the user cannot connect to their action.
    """
    max_per_day = cadence.get("max_per_day")
    if (
        not isinstance(max_per_day, int)
        or isinstance(max_per_day, bool)
        or not (1 <= max_per_day <= MAX_PER_DAY_CEILING)
    ):
        raise CadenceInvalid(f"max_per_day must be 1-{MAX_PER_DAY_CEILING}")

    days = cadence.get("days")
    if (
        not isinstance(days, list)
        or not days
        or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)
    ):
        raise CadenceInvalid("days must be a non-empty list of integers 0-6 (Mon-Sun)")

    start = _parse_hhmm(cadence.get("window_start"), "window_start")
    end = _parse_hhmm(cadence.get("window_end"), "window_end")
    if end <= start:
        raise CadenceInvalid("window_end must be after window_start")

    try:
        ZoneInfo(cadence.get("timezone", ""))
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise CadenceInvalid(f"Unknown timezone: {cadence.get('timezone')!r}") from exc

    return {
        "max_per_day": max_per_day,
        "days": sorted(set(days)),
        "window_start": start.strftime("%H:%M"),
        "window_end": end.strftime("%H:%M"),
        "timezone": cadence["timezone"],
    }


def _slot_offsets(start: time, end: time, count: int) -> list[timedelta]:
    """Offsets from window_start for `count` evenly spaced slots.

    Evenly spaced, not all at window_start: N emails at the same instant is the
    burst this exists to prevent. With 09:00-17:00 and max_per_day=10, slots land
    every 48 minutes.
    """
    span = datetime.combine(datetime.min, end) - datetime.combine(datetime.min, start)
    step = span / count
    return [step * index for index in range(count)]


def next_slot(cadence: dict, scheduled: list[datetime], now: datetime) -> datetime:
    """The earliest UTC instant satisfying the cadence, given what is queued.

    Walks forward day by day in the user's timezone, skipping days not in
    `cadence["days"]` and days already at `max_per_day`, then returns the first
    free evenly-spaced slot at or after `now`.

    Fails fast against a backlog the horizon cannot possibly absorb: with
    `max_per_day=1` on Sundays only, the 90-day horizon holds roughly a dozen
    slots total, so a queue of 200 must raise CadenceUnsatisfiable rather than
    scanning all 90 days to discover the same thing one slot at a time (or,
    worse, than silently scheduling far past the horizon by extending the scan
    itself). The check compares `len(scheduled)` — the caller's own backlog
    count — against the horizon's total capacity, independent of exactly which
    dates those backlog entries land on.
    """
    cadence = validate_cadence(cadence)
    zone = ZoneInfo(cadence["timezone"])
    start = _parse_hhmm(cadence["window_start"], "window_start")
    end = _parse_hhmm(cadence["window_end"], "window_end")
    per_day = cadence["max_per_day"]
    days = cadence["days"]

    local_now = now.astimezone(zone)

    valid_days_in_horizon = sum(
        1
        for day_index in range(HORIZON_DAYS)
        if (local_now + timedelta(days=day_index)).date().weekday() in days
    )
    capacity = valid_days_in_horizon * per_day
    if len(scheduled) >= capacity:
        raise CadenceUnsatisfiable(
            f"No cadence slot available within {HORIZON_DAYS} days "
            f"({per_day}/day on days {days}, {len(scheduled)} already queued "
            f"meets or exceeds the horizon's capacity of {capacity})"
        )

    # Group existing sends by LOCAL calendar day — "10 per day" means the user's
    # day, not a UTC day.
    taken: dict[object, set[datetime]] = {}
    for slot in scheduled:
        local = slot.astimezone(zone)
        taken.setdefault(local.date(), set()).add(slot)

    offsets = _slot_offsets(start, end, per_day)

    for day_index in range(HORIZON_DAYS):
        day = (local_now + timedelta(days=day_index)).date()

        if day.weekday() not in days:
            continue

        already = taken.get(day, set())
        if len(already) >= per_day:
            continue

        for offset in offsets:
            # Build the local wall-clock instant, then convert to UTC. On a
            # spring-forward day a skipped local time normalises to a real
            # instant; on fall-back, distinct offsets still yield distinct UTC
            # instants because the offsets themselves differ.
            candidate_local = datetime.combine(day, start, tzinfo=zone) + offset
            candidate = candidate_local.astimezone(UTC)

            if candidate < now:
                continue
            if candidate in already:
                continue
            return candidate

    raise CadenceUnsatisfiable(
        f"No cadence slot available within {HORIZON_DAYS} days "
        f"({per_day}/day on days {days}, {len(scheduled)} already queued)"
    )
