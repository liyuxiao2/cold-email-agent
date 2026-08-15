import pytest

ADMIN_ONLY = [
    "/api/pipeline/discovery",
    "/api/pipeline/research",
    "/api/pipeline/drafting",
    "/api/dlq/retry",
]

USER_ROUTES = [
    ("GET", "/api/outreach"),
    ("GET", "/api/outreach/drafts"),
    ("GET", "/api/companies"),
    ("GET", "/api/pipeline/stats"),
    ("GET", "/api/dlq"),
]


@pytest.mark.asyncio
async def test_health_stays_public(client):
    """Cloud Run's health check is unauthenticated. Gating this takes
    production down, so it is a regression guard."""
    assert (await client.get("/api/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_reject_anonymous(client, path):
    assert (await client.post(path)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_reject_plain_users(user_client, path):
    assert (await user_client.post(path)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_ONLY)
async def test_admin_routes_accept_admins(admin_client, path, monkeypatch):
    # 200 requires a reachable Celery broker: unlike leads.py's approve/
    # regenerate, these routes catch a dispatch failure and re-raise as HTTP
    # 500 rather than tolerating it. Redis is reachable here (running locally
    # via `make up`, or as CI's service container), so 200 is the right
    # expectation.
    assert (await admin_client.post(path)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", USER_ROUTES)
async def test_user_routes_reject_anonymous(client, method, path):
    response = await client.request(method, path)
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", USER_ROUTES)
async def test_user_routes_accept_authenticated_users(user_client, method, path):
    response = await user_client.request(method, path)
    assert response.status_code == 200
