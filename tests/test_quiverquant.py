"""QuiverQuant client tests with mocked HTTP.

Patterns demonstrated here apply to the other scraper modules
(`congress_gov.py`, `house.py`, `senate_efd_api.py`, etc.):

* Use `unittest.mock.patch.object` on `requests.Session.get` to intercept
  outbound HTTP without touching the network.
* Pass synthetic JSON payloads via a Mock response.
* Assert the client correctly parses the response shape, applies the
  `limit` parameter, and surfaces auth failures.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion.quiverquant import (
    BULK_CONGRESS_ENDPOINT,
    HISTORICAL_HOUSE_ENDPOINT,
    QuiverQuantClient,
)


def _mock_response(payload, status_code: int = 200) -> MagicMock:
    """Build a Mock that imitates `requests.Response`."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=status_code))
        response.raise_for_status.side_effect = err
    else:
        response.raise_for_status.return_value = None
    return response


class TestQuiverQuantClientInit:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("QUIVERQUANT_API_KEY", raising=False)
        with pytest.raises(ValueError, match="QuiverQuant API key not provided"):
            QuiverQuantClient(api_key=None)

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("QUIVERQUANT_API_KEY", "from-env")
        client = QuiverQuantClient(api_key="explicit")
        assert client.api_key == "explicit"

    def test_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setenv("QUIVERQUANT_API_KEY", "env-key")
        client = QuiverQuantClient()
        assert client.api_key == "env-key"

    def test_session_includes_bearer_auth(self, monkeypatch):
        monkeypatch.setenv("QUIVERQUANT_API_KEY", "test-token")
        client = QuiverQuantClient()
        assert client.session.headers["Authorization"] == "Bearer test-token"


class TestFetchTrades:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("QUIVERQUANT_API_KEY", "test-token")
        return QuiverQuantClient()

    def test_bulk_endpoint_hit(self, client):
        payload = [
            {"Representative": "Alice Example", "Ticker": "AAPL", "Transaction": "Purchase"},
            {"Representative": "Bob Test", "Ticker": "MSFT", "Transaction": "Sale"},
        ]
        with patch.object(client.session, "get", return_value=_mock_response(payload)) as mock_get:
            trades = client.get_bulk_congress_trades()

        mock_get.assert_called_once_with(BULK_CONGRESS_ENDPOINT, timeout=60)
        assert len(trades) == 2
        assert trades[0]["Ticker"] == "AAPL"

    def test_limit_truncates_response(self, client):
        payload = [{"Ticker": f"T{i}"} for i in range(20)]
        with patch.object(client.session, "get", return_value=_mock_response(payload)):
            trades = client.get_historical_house_trades(limit=5)
        assert len(trades) == 5
        assert trades[-1]["Ticker"] == "T4"

    def test_returns_empty_list_for_non_list_payload(self, client):
        # The API sometimes returns an error object instead of a list.
        with patch.object(
            client.session, "get", return_value=_mock_response({"error": "rate limited"})
        ):
            assert client.get_historical_house_trades() == []

    def test_endpoint_routing(self, client):
        """Each get_* method should hit its own URL."""
        cases = [
            (client.get_bulk_congress_trades, BULK_CONGRESS_ENDPOINT),
            (client.get_historical_house_trades, HISTORICAL_HOUSE_ENDPOINT),
        ]
        for method, expected_url in cases:
            with patch.object(client.session, "get", return_value=_mock_response([])) as mock_get:
                method()
            assert mock_get.call_args.args[0] == expected_url

    def test_http_error_returns_empty(self, client):
        """A 500 response is logged but not raised — caller gets empty list."""
        with patch.object(client.session, "get", return_value=_mock_response([], status_code=500)):
            trades = client.get_bulk_congress_trades()
        assert trades == []
