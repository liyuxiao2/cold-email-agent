from unittest.mock import MagicMock, patch

import pytest

from cold_email.workers.discovery import extract_leads


def _stub_firecrawl(monkeypatch, leads: list[dict]):
    """Stub Firecrawl Extract so discovery_task doesn't hit the network."""
    fake_response = MagicMock()
    fake_response.data = {"leads": leads}
    mock_firecrawl = MagicMock()
    mock_firecrawl.return_value.extract.return_value = fake_response
    monkeypatch.setattr("cold_email.workers.discovery.discovery.Firecrawl", mock_firecrawl)


def test_extract_leads_truncates_to_limit():
    """extract_leads should return at most `limit` results."""
    fake_leads = [{"company_name": f"Co{i}"} for i in range(50)]
    fake_response = MagicMock()
    fake_response.data = {"leads": fake_leads}

    with patch("cold_email.workers.discovery.discovery.Firecrawl") as MockFC:
        MockFC.return_value.extract.return_value = fake_response
        result = extract_leads(["https://example.com"], limit=5)
        MockFC.return_value.extract.assert_called_once()

    assert result == fake_leads[:5]


def test_extract_leads_returns_empty_on_no_leads():
    """extract_leads should return [] when Firecrawl returns no leads key."""
    fake_response = MagicMock()
    fake_response.data = {}

    with patch("cold_email.workers.discovery.discovery.Firecrawl") as MockFC:
        MockFC.return_value.extract.return_value = fake_response
        result = extract_leads(["https://example.com"], limit=20)

    assert result == []


@pytest.mark.asyncio
async def test_new_companies_start_at_found(async_session, monkeypatch, sync_session_for):
    from cold_email.database import RESEARCH_FOUND, Company

    _stub_firecrawl(monkeypatch, [{"company_name": "NewCo", "company_url": "https://new.co"}])

    from cold_email.workers.discovery.discovery import discovery_task

    discovery_task()

    from sqlalchemy import select

    company = (
        await async_session.execute(select(Company).where(Company.company_name == "NewCo"))
    ).scalar_one()
    assert company.research_status == RESEARCH_FOUND


@pytest.mark.asyncio
async def test_dedupes_against_existing_companies(async_session, monkeypatch, sync_session_for):
    """Dedup now protects the GLOBAL pool: a duplicate would give two users
    different contact pools for the same company."""
    from cold_email.database import Company

    async_session.add(Company(company_name="ExistingCo"))
    await async_session.commit()

    _stub_firecrawl(monkeypatch, [{"company_name": "ExistingCo", "company_url": "https://e.co"}])

    from cold_email.workers.discovery.discovery import discovery_task

    result = discovery_task()
    assert result["saved"] == 0

    from sqlalchemy import func, select

    assert (
        await async_session.execute(select(func.count()).select_from(Company))
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_dispatches_research_with_a_company_id(async_session, monkeypatch, sync_session_for):
    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.discovery.discovery.research_task",
        type("T", (), {"delay": staticmethod(lambda cid: dispatched.append(cid))}),
    )
    _stub_firecrawl(monkeypatch, [{"company_name": "NewCo", "company_url": "https://new.co"}])

    from cold_email.workers.discovery.discovery import discovery_task

    discovery_task()
    assert len(dispatched) == 1
