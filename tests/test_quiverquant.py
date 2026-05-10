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

from src.ingestion.quiverquant import (
    BULK_CONGRESS_ENDPOINT,
    HISTORICAL_HOUSE_ENDPOINT,
    QuiverQuantClient,
    parse_amount_range,
)


def _mock_response(payload, status_code: int = 200, headers: dict | None = None) -> MagicMock:
    """Build a Mock that imitates `requests.Response`."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = payload
    return response


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip retry backoff sleeps so retry tests run instantly."""
    monkeypatch.setattr("src.ingestion.quiverquant.time.sleep", lambda *_: None)


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

        mock_get.assert_called_once_with(BULK_CONGRESS_ENDPOINT, timeout=60, params=None)
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

    def test_persistent_5xx_returns_empty_after_retries(self, client):
        """A 500 that doesn't recover yields [] after exhausting backoff."""
        with patch.object(
            client.session, "get", return_value=_mock_response([], status_code=500)
        ) as mock_get:
            trades = client.get_bulk_congress_trades()
        assert trades == []
        # _RETRY_BACKOFF_SECONDS has 4 entries → 4 attempts.
        assert mock_get.call_count == 4

    def test_429_then_success_recovers(self, client):
        """A transient 429 is retried; eventual 200 returns the data."""
        responses = [
            _mock_response([], status_code=429, headers={"Retry-After": "1"}),
            _mock_response([{"Ticker": "AAPL"}]),
        ]
        with patch.object(client.session, "get", side_effect=responses) as mock_get:
            trades = client.get_bulk_congress_trades()
        assert trades == [{"Ticker": "AAPL"}]
        assert mock_get.call_count == 2

    def test_401_does_not_retry(self, client):
        """Auth failure is final — no point retrying with a bad key."""
        with patch.object(
            client.session, "get", return_value=_mock_response([], status_code=401)
        ) as mock_get:
            trades = client.get_bulk_congress_trades()
        assert trades == []
        assert mock_get.call_count == 1


# ---------------- ingestion integration ----------------


class TestIngestTrades:
    """End-to-end ingestion tests using the in-memory db_session fixture."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("QUIVERQUANT_API_KEY", "test-token")
        return QuiverQuantClient()

    def test_ingest_parses_range_amount(self, client, db_session):
        """The whole point of the parse_amount_range fix: range amounts that
        used to silently turn into (None, None) now land as proper bounds."""
        from src.db.models import Chamber, Member, Party, Transaction

        member = Member(
            bioguide_id="X000001",
            first_name="Range",
            last_name="Test",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
            in_office=True,
        )
        db_session.add(member)
        db_session.commit()

        trade = {
            "Name": "Range Test",
            "Ticker": "AAPL",
            "Transaction": "Purchase",
            "Amount": "$1,001 - $15,000",
            "Traded": "2024-03-15",
            "BioGuideID": "X000001",
        }
        result = client._ingest_trade(db_session, trade, source="test")
        db_session.commit()
        assert result == "imported"

        txn = db_session.query(Transaction).filter_by(ticker="AAPL").one()
        assert txn.amount_min == 1001
        assert txn.amount_max == 15000

    def test_auto_creates_with_proper_enum_types(self, client, db_session):
        """Members auto-created from trade data must have Party/Chamber as
        enum members, not raw strings — the old version assigned string
        which broke under Postgres' enum columns."""
        from src.db.models import Chamber, Member, Party

        trade = {
            "Name": "Newly Encountered",
            "Ticker": "NVDA",
            "Transaction": "Purchase",
            "Amount": "$1,001 - $15,000",
            "Traded": "2024-03-15",
            "BioGuideID": "Y999999",
            "Party": "R",
            "State": "TX",
            "Chamber": "Senate",
        }
        result = client._ingest_trade(db_session, trade, source="test")
        db_session.commit()
        assert result == "imported"

        m = db_session.query(Member).filter_by(bioguide_id="Y999999").one()
        assert m.party is Party.REPUBLICAN
        assert m.chamber is Chamber.SENATE
        assert m.state == "TX"

    def test_skips_when_no_bioguide_and_no_db_match(self, client, db_session):
        """Without a BioGuideID and without a name match, refuse to
        auto-create — risk of duplicating an existing member by spelling."""
        from src.db.models import Member

        before = db_session.query(Member).count()
        trade = {
            "Name": "Anonymous Person",  # not in DB, no bioguide
            "Ticker": "MSFT",
            "Transaction": "Sale",
            "Amount": "$50,001 - $100,000",
            "Traded": "2024-04-01",
        }
        result = client._ingest_trade(db_session, trade, source="test")
        assert result == "error"
        assert db_session.query(Member).count() == before

    def test_bioguide_match_takes_priority_over_name(self, client, db_session):
        """When a BioGuideID is supplied and matches an existing member,
        we use that — even if the supplied Name is slightly different."""
        from src.db.models import Chamber, Member, Party, Transaction

        member = Member(
            bioguide_id="P000001",
            first_name="Robert",
            last_name="Smith",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="OH",
            in_office=True,
        )
        db_session.add(member)
        db_session.commit()

        trade = {
            "Name": "Bob Smith Jr.",  # different rendering of same person
            "Ticker": "GOOG",
            "Transaction": "Purchase",
            "Amount": "$15,001 - $50,000",
            "Traded": "2024-05-01",
            "BioGuideID": "P000001",
        }
        result = client._ingest_trade(db_session, trade, source="test")
        db_session.commit()
        assert result == "imported"

        # No new member was created — the existing one was used.
        assert db_session.query(Member).filter_by(bioguide_id="P000001").count() == 1
        txn = db_session.query(Transaction).filter_by(ticker="GOOG").one()
        assert txn.disclosure.member_id == member.id


# ---------------- parse_amount_range ----------------


class TestParseAmountRange:
    """The previous implementation called float() on the raw string, which
    silently dropped every range-format Amount (the dominant House PTR
    shape). These tests pin the new range-aware parser."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$1,001 - $15,000", (1001.0, 15000.0)),
            ("$15,001 - $50,000", (15001.0, 50000.0)),
            ("1001 - 15000", (1001.0, 15000.0)),
            ("$1,000,001 - $5,000,000", (1000001.0, 5000000.0)),
            ("50000", (50000.0, 50000.0)),
            ("$50,000", (50000.0, 50000.0)),
            ("$50,000.50", (50000.50, 50000.50)),
            ("", (None, None)),
            (None, (None, None)),
            ("not a number", (None, None)),
            ("   ", (None, None)),
        ],
    )
    def test_parse(self, raw, expected):
        assert parse_amount_range(raw) == expected
