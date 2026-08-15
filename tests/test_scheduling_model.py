import pytest

from cold_email.database import OUTREACH_SENDING


def test_sending_status_exists():
    """Celery guarantees at-least-once task delivery, so a scanner over rows
    that only leave on success will eventually dispatch one twice. 'approved'
    cannot express 'already handed to a worker'."""
    assert OUTREACH_SENDING == "sending"


@pytest.mark.asyncio
async def test_send_cadence_defaults_to_null(async_session, admin_user):
    """NULL means send immediately on approve — no cadence configured."""
    assert admin_user.send_cadence is None


@pytest.mark.asyncio
async def test_send_cadence_round_trips_as_jsonb(async_session, admin_user):
    admin_user.send_cadence = {
        "max_per_day": 10,
        "days": [0, 1, 2, 3, 4],
        "window_start": "09:00",
        "window_end": "17:00",
        "timezone": "America/Toronto",
    }
    await async_session.commit()
    await async_session.refresh(admin_user)
    assert admin_user.send_cadence["max_per_day"] == 10
    assert admin_user.send_cadence["days"] == [0, 1, 2, 3, 4]
