from unittest.mock import MagicMock, patch

from cold_email.workers.discovery import extract_leads


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
