from unittest.mock import MagicMock, patch

from cold_email.workers.research.helpers.extraction import (
    find_company_url,
    scrape_website,
    select_best_url,
)

from cold_email.database import Lead
from cold_email.workers.research.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_select_best_url():
    lead = Lead(company_name="Acme Corp")
    results = [
        {"url": "https://linkedin.com/company/acme"},
        {"url": "https://acmecorp.com/about"},
        {"url": "https://someothersite.com"},
    ]
    best_url = select_best_url(results, lead)
    # linkedin.com is in the aggregator blocklist, acmecorp.com slug-matches → wins
    assert best_url == "https://acmecorp.com/about"


def test_find_company_url():
    lead = Lead(company_name="Acme Corp", funding_stage="Seed")
    mock_response = MagicMock()
    mock_response.json.return_value = {"web": {"results": [{"url": "https://acme.com"}]}}
    with patch(
        "cold_email.workers.research.extraction.httpx.get", return_value=mock_response
    ) as mock_get:
        url = find_company_url(lead)
        mock_get.assert_called_once()
        assert url == "https://acme.com"


def test_scrape_website_soup():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = (
        b"<html><body><script>console.log('test')</script><p>"
        + b"Hello World " * 30
        + b"</p></body></html>"
    )
    with patch("requests.get", return_value=mock_response) as mock_get:
        text = scrape_website("https://example.com")
        mock_get.assert_called_once()
        # Script tags are stripped; plain text content is extracted
        assert "Hello World" in text
        assert "console.log" not in text


def test_scrape_website_firecrawl_fallback():
    mock_response = MagicMock()
    # Short content triggers Firecrawl fallback
    mock_response.status_code = 200
    mock_response.content = b"<html><body><p>Hi</p></body></html>"

    mock_fc_response = MagicMock()
    mock_fc_response.markdown = "Hello from Firecrawl fallback!"

    with (
        patch("requests.get", return_value=mock_response),
        patch("cold_email.workers.research.extraction.FirecrawlApp") as MockFC,
    ):
        MockFC.return_value.scrape.return_value = mock_fc_response
        text = scrape_website("https://example.com")
        assert text == "Hello from Firecrawl fallback!"


def test_research_task_lead_not_found():
    # If lead is not found, research_task should return status: failed
    with patch("cold_email.workers.research.research.fetch_lead", return_value=None):
        result = research_task.apply(args=[FAKE_UUID]).get(propagate=True)
        assert result == {"status": "failed", "error": "Lead not found"}
