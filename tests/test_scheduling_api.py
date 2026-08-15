import pytest

CADENCE = {
    "max_per_day": 2,
    "days": [0, 1, 2, 3, 4, 5, 6],
    "window_start": "09:00",
    "window_end": "17:00",
    "timezone": "America/Toronto",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"max_per_day": 0},
        {"max_per_day": 51},
        {"days": []},
        {"days": [9]},
        {"window_start": "18:00", "window_end": "09:00"},
        {"timezone": "Not/AZone"},
    ],
)
async def test_cadence_validation_rejects_bad_fields(user_client, override):
    response = await user_client.put("/api/cadence", json={**CADENCE, **override})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cadence_crud(user_client):
    assert (await user_client.get("/api/cadence")).json()["cadence"] is None

    await user_client.put("/api/cadence", json=CADENCE)
    assert (await user_client.get("/api/cadence")).json()["cadence"]["max_per_day"] == 2

    await user_client.delete("/api/cadence")
    assert (await user_client.get("/api/cadence")).json()["cadence"] is None
