import pytest


@pytest.mark.asyncio
async def test_requires_auth(client):
    assert (await client.get("/api/companies")).status_code == 401


@pytest.mark.asyncio
async def test_only_researched_companies_appear(user_client, pool_fixture):
    names = {c["company_name"] for c in (await user_client.get("/api/companies")).json()["items"]}
    assert "ResearchedCo" in names
    assert "FoundCo" not in names
    assert "FailedCo" not in names


@pytest.mark.asyncio
async def test_companies_without_an_eligible_contact_are_hidden(user_client, pool_fixture):
    names = {c["company_name"] for c in (await user_client.get("/api/companies")).json()["items"]}
    assert "GenericOnlyCo" not in names


@pytest.mark.asyncio
async def test_exhausted_companies_are_hidden(user_client, exhausted_company):
    names = {c["company_name"] for c in (await user_client.get("/api/companies")).json()["items"]}
    assert "ExhaustedCo" not in names


@pytest.mark.asyncio
async def test_already_targeted_is_hidden_from_me_but_visible_to_others(
    user_client, admin_client, targeted_by_user_company
):
    """The single most important test in this stack: it is the difference between
    a shared pool and a broken one.

    A LEFT JOIN on outreach without the user predicate would hide the company
    from EVERYONE the moment one person targeted it.
    """
    mine = {c["company_name"] for c in (await user_client.get("/api/companies")).json()["items"]}
    assert "TargetedCo" not in mine

    theirs = {c["company_name"] for c in (await admin_client.get("/api/companies")).json()["items"]}
    assert "TargetedCo" in theirs


@pytest.mark.asyncio
async def test_no_email_addresses_are_exposed(user_client, pool_fixture):
    """The pool is the product's inventory. A scrapeable list of verified founder
    emails handed to every signup is a lead-list leak."""
    body = (await user_client.get("/api/companies")).text
    assert "@" not in body

    company_id = (await user_client.get("/api/companies")).json()["items"][0]["id"]
    detail = (await user_client.get(f"/api/companies/{company_id}")).text
    assert "cto@researched.co" not in detail


@pytest.mark.asyncio
async def test_detail_includes_contact_summaries_without_addresses(user_client, pool_fixture):
    company_id = (await user_client.get("/api/companies")).json()["items"][0]["id"]
    detail = (await user_client.get(f"/api/companies/{company_id}")).json()

    assert detail["research"]["hook"] is not None
    assert len(detail["contacts"]) >= 1
    for contact in detail["contacts"]:
        assert set(contact) == {"first_name", "position", "is_founder"}


@pytest.mark.asyncio
async def test_contact_count_reflects_availability(user_client, pool_fixture):
    item = next(
        c
        for c in (await user_client.get("/api/companies")).json()["items"]
        if c["company_name"] == "ResearchedCo"
    )
    assert item["contact_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("?industry=Fintech", {"ResearchedCo"}),
        ("?search=Research", {"ResearchedCo"}),
        ("?headcount_min=100", set()),
        ("?has_founder_contact=true", {"ResearchedCo"}),
    ],
)
async def test_filters(user_client, pool_fixture, query, expected):
    names = {
        c["company_name"] for c in (await user_client.get(f"/api/companies{query}")).json()["items"]
    }
    assert names & {"ResearchedCo", "GenericOnlyCo"} == expected
