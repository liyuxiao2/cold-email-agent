"""Cadence slot arithmetic — pure functions, the highest-value tests in the stack.

DST is the reason these exist. America/Toronto has a day with no 02:30 and a day
with two, so any local-time reasoning is wrong twice a year.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cold_email.cadence import (
    CadenceInvalid,
    CadenceUnsatisfiable,
    next_slot,
    validate_cadence,
)

TORONTO = ZoneInfo("America/Toronto")

WEEKDAYS_9_TO_5 = {
    "max_per_day": 4,
    "days": [0, 1, 2, 3, 4],
    "window_start": "09:00",
    "window_end": "17:00",
    "timezone": "America/Toronto",
}


def _local(slot: datetime) -> datetime:
    return slot.astimezone(TORONTO)


# ------------------------------------------------------------------ basic slots


def test_first_slot_opens_the_window():
    # Friday 2026-08-14, 06:00 local — before the window.
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)
    slot = _local(next_slot(WEEKDAYS_9_TO_5, [], now))
    assert (slot.hour, slot.minute) == (9, 0)
    assert slot.date() == datetime(2026, 8, 14).date()


def test_returns_utc():
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)
    assert next_slot(WEEKDAYS_9_TO_5, [], now).tzinfo is UTC


def test_slots_are_evenly_spaced_across_the_window():
    """Ten emails at exactly 09:00:00 is the burst the cadence exists to
    prevent, so slots spread rather than stacking at window_start."""
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)

    scheduled, times = [], []
    for _ in range(4):
        slot = next_slot(WEEKDAYS_9_TO_5, scheduled, now)
        scheduled.append(slot)
        times.append(_local(slot))

    assert [(t.hour, t.minute) for t in times] == [(9, 0), (11, 0), (13, 0), (15, 0)]


def test_day_rolls_over_once_max_per_day_is_reached():
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)
    scheduled = [next_slot(WEEKDAYS_9_TO_5, [], now)]
    for _ in range(3):
        scheduled.append(next_slot(WEEKDAYS_9_TO_5, scheduled, now))

    fifth = _local(next_slot(WEEKDAYS_9_TO_5, scheduled, now))
    assert fifth.date() == datetime(2026, 8, 17).date()  # Monday, skipping the weekend
    assert (fifth.hour, fifth.minute) == (9, 0)


def test_non_cadence_days_are_skipped():
    # Saturday 2026-08-15.
    now = datetime(2026, 8, 15, 10, 0, tzinfo=TORONTO).astimezone(UTC)
    slot = _local(next_slot(WEEKDAYS_9_TO_5, [], now))
    assert slot.weekday() == 0  # Monday
    assert slot.date() == datetime(2026, 8, 17).date()


def test_mid_window_now_does_not_schedule_in_the_past():
    now = datetime(2026, 8, 14, 13, 30, tzinfo=TORONTO).astimezone(UTC)
    assert next_slot(WEEKDAYS_9_TO_5, [], now) >= now


def test_slots_strictly_increase_across_many_days():
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)
    scheduled = []
    for _ in range(20):
        scheduled.append(next_slot(WEEKDAYS_9_TO_5, scheduled, now))

    assert scheduled == sorted(scheduled)
    assert len(set(scheduled)) == 20


# ------------------------------------------------------------------------- DST


def test_spring_forward_produces_valid_increasing_instants():
    """2026-03-08: America/Toronto skips 02:00-03:00 local. A window spanning it
    must still yield valid, ordered UTC instants."""
    cadence = {
        **WEEKDAYS_9_TO_5,
        "window_start": "01:00",
        "window_end": "05:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "max_per_day": 4,
    }
    now = datetime(2026, 3, 8, 0, 0, tzinfo=TORONTO).astimezone(UTC)

    scheduled = []
    for _ in range(4):
        scheduled.append(next_slot(cadence, scheduled, now))

    assert scheduled == sorted(scheduled)
    assert len(set(scheduled)) == 4
    for slot in scheduled:
        assert slot.tzinfo is UTC
        _local(slot)  # must not raise


def test_fall_back_produces_no_duplicate_utc_slots():
    """2026-11-01: 01:00-02:00 local happens twice. Two distinct UTC instants
    map to the same local time, so naive local arithmetic would emit duplicates."""
    cadence = {
        **WEEKDAYS_9_TO_5,
        "window_start": "00:30",
        "window_end": "04:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "max_per_day": 4,
    }
    now = datetime(2026, 11, 1, 0, 0, tzinfo=TORONTO).astimezone(UTC)

    scheduled = []
    for _ in range(4):
        scheduled.append(next_slot(cadence, scheduled, now))

    assert len(set(scheduled)) == 4


def test_a_non_utc_cadence_converts_back_to_the_intended_local_time():
    cadence = {**WEEKDAYS_9_TO_5, "timezone": "Asia/Tokyo"}
    tokyo = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 8, 14, 6, 0, tzinfo=tokyo).astimezone(UTC)

    slot = next_slot(cadence, [], now).astimezone(tokyo)
    assert (slot.hour, slot.minute) == (9, 0)


# --------------------------------------------------------------- unsatisfiable


def test_pathological_cadence_with_a_large_backlog_raises():
    """max_per_day=1 on Sundays only, with 200 approved: failing loudly beats
    silently scheduling a send for 2028."""
    cadence = {
        "max_per_day": 1,
        "days": [6],
        "window_start": "09:00",
        "window_end": "10:00",
        "timezone": "America/Toronto",
    }
    now = datetime(2026, 8, 14, 6, 0, tzinfo=TORONTO).astimezone(UTC)

    scheduled = [now + timedelta(days=7 * i) for i in range(200)]
    with pytest.raises(CadenceUnsatisfiable):
        next_slot(cadence, scheduled, now)


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "override",
    [
        {"max_per_day": 0},
        {"max_per_day": 51},
        {"days": []},
        {"days": [7]},
        {"days": [-1]},
        {"window_start": "17:00", "window_end": "09:00"},
        {"window_start": "09:00", "window_end": "09:00"},
        {"timezone": "Mars/Olympus_Mons"},
        {"timezone": "EST5EDT-nonsense"},
        {"window_start": "9am"},
    ],
)
def test_validation_rejects_bad_fields(override):
    with pytest.raises(CadenceInvalid):
        validate_cadence({**WEEKDAYS_9_TO_5, **override})


def test_validation_accepts_a_good_cadence():
    assert validate_cadence(WEEKDAYS_9_TO_5)["max_per_day"] == 4
