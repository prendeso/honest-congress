"""A per-company sweep survives one company's failure, and stops on time.

Nightly run 238 (2026-09-28) lost two feeds, both behind a green tick:

    requests.exceptions.HTTPError: 500 Server Error: Internal Server Error for
    url: https://api.usaspending.gov/api/v2/search/spending_by_transaction/

    ##[error]The action 'Ingest lobbying disclosures' has timed out after 40 minutes.

The first was ONE company's search. A 500 is deliberately not retried, which is
right, but the exception escaped the loop and ended the sweep for every company
after it. The second was a step killed by GitHub mid-company: no summary, no
line saying how far it got, and -- because the sweep is alphabetical -- the
next slow night stops in the same place, so the end of the alphabet is never
refreshed.

These tests pin the three fixes: a refused company is skipped and named, a
source that refuses everything is reported as down, and a wall-clock budget
stops the sweep cleanly while rotating where it starts.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.db.models import LobbyingDisclosure
from src.ingestion._helpers import CONSECUTIVE_REFUSALS_BEFORE_STOPPING, sweep_order
from src.ingestion.lda import LDAClient, ingest_lobbying_disclosures
from src.ingestion.rate_limit import RequestBudgetExhausted, ThrottledClient
from src.ingestion.sec_tickers import TickerResolver
from src.ingestion.usaspending import ingest_government_contracts

FIXTURES = Path(__file__).parent / "fixtures"
SEC = FIXTURES / "sec" / "company_tickers.json"
AWARDS = FIXTURES / "usaspending" / "spending_by_transaction_2024.json"
NORTHROP = FIXTURES / "lda" / "northrop_filings.json"


def _response(payload, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} Server Error"
        )
    else:
        response.raise_for_status.return_value = None
    return response


@pytest.fixture
def resolver() -> TickerResolver:
    session = MagicMock()
    session.get.return_value = _response(json.loads(SEC.read_text()))
    return TickerResolver(session=session)


def _server_error(*_args, **_kwargs):
    raise requests.exceptions.HTTPError("500 Server Error: Internal Server Error")


# ---------------- the contract feed ----------------


class TestTheContractSweepSurvivesARefusal:
    def test_a_500_for_one_company_does_not_end_the_sweep(self, resolver):
        awards = json.loads(AWARDS.read_text())["results"]
        calls = []

        def fetch(start, end, *, recipient, pages, client):
            calls.append(recipient)
            if len(calls) == 1:
                _server_error()
            return awards

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        with patch("src.ingestion.usaspending.fetch_awards", side_effect=fetch):
            with patch("src.ingestion.usaspending.commit_or_recover", return_value=True):
                result = ingest_government_contracts(
                    db, "2024-01-01", "2024-12-31", tickers=["BA", "GD"], resolver=resolver
                )

        assert len(calls) == 2, "the second company must still be asked about"
        assert result["companies_the_source_refused"] == ["BA"]
        assert result["source_down"] is False
        assert result["stopped_early"] is False
        assert result["imported"] > 0

    def test_a_source_refusing_everything_is_reported_as_down(self, db_session, resolver):
        # Enough real, registered tickers to trip the breaker.
        register = json.loads(SEC.read_text())
        tickers = sorted({row["ticker"] for row in register.values()})
        assert len(tickers) > CONSECUTIVE_REFUSALS_BEFORE_STOPPING

        with patch("src.ingestion.usaspending.fetch_awards", side_effect=_server_error) as fetch:
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2024-12-31", tickers=tickers, resolver=resolver
            )

        assert result["source_down"] is True
        assert result["stopped_early"] is True
        assert fetch.call_count == CONSECUTIVE_REFUSALS_BEFORE_STOPPING

    def test_the_cli_exits_non_zero_when_the_source_is_down(self, resolver):
        """An outage reported as success is a dead feed behind a green tick."""
        from src import cli

        down = {
            "tickers_queried": 10,
            "tickers_without_a_registered_name": 0,
            "fetched": 0,
            "imported": 0,
            "duplicates": 0,
            "money_taken_back": 0,
            "rejected_wrong_company": 0,
            "rejected_names": {},
            "connection_losses": 0,
            "companies_lost_to_the_database": [],
            "companies_the_source_refused": ["A"] * 10,
            "source_down": True,
            "stopped_early": True,
        }
        args = MagicMock(start="2024-01-01", end=None, pages_per_company=1, max_requests=None)
        args.max_minutes = None
        with patch("src.ingestion.usaspending.ingest_government_contracts", return_value=down):
            with patch.object(cli, "get_db"):
                with pytest.raises(SystemExit) as exit_:
                    cli.cmd_ingest_contracts(args)
        assert exit_.value.code == 1


# ---------------- the lobbying feed ----------------


class TestTheLobbyingSweepSurvivesARefusal:
    def test_a_500_for_one_company_does_not_end_the_sweep(self, db_session, resolver):
        northrop = json.loads(NORTHROP.read_text())
        northrop["next"] = None
        session = MagicMock()
        session.get.side_effect = [_response({}, 500), _response(northrop)]
        client = LDAClient(session=session, sleeper=lambda _: None)

        result = ingest_lobbying_disclosures(
            db_session, 2024, tickers=["BA", "NOC"], resolver=resolver, client=client
        )

        assert session.get.call_count == 2
        assert result["companies_the_source_refused"] == ["BA"]
        assert result["source_down"] is False
        assert db_session.query(LobbyingDisclosure).count() == result["imported"] > 0


# ---------------- the time budget ----------------


class TestTheTimeBudget:
    def _client(self, clock_values, max_seconds):
        clock = iter(clock_values)
        client = ThrottledClient(
            limit=1000,
            session=MagicMock(),
            max_seconds=max_seconds,
            sleeper=lambda _: None,
            clock=lambda: next(clock),
        )
        client.session.get.return_value = _response({})
        return client

    def test_a_request_inside_the_budget_goes_out(self):
        # construction, deadline check, limiter
        client = self._client([0.0, 10.0, 10.0], max_seconds=60)
        client.get("/x")
        assert client.session.get.call_count == 1

    def test_a_request_past_the_budget_stops_cleanly(self):
        client = self._client([0.0, 61.0], max_seconds=60)
        with pytest.raises(RequestBudgetExhausted, match="time limit"):
            client.get("/x")
        assert client.session.get.call_count == 0

    def test_no_budget_means_no_limit(self):
        client = ThrottledClient(limit=1000, session=MagicMock(), sleeper=lambda _: None)
        client.session.get.return_value = _response({})
        client.get("/x")
        assert client.session.get.call_count == 1

    def test_the_lda_client_forwards_the_budget(self):
        clock = iter([0.0, 3600.0])
        client = LDAClient(
            session=MagicMock(), max_seconds=60, sleeper=lambda _: None, clock=lambda: next(clock)
        )
        with pytest.raises(RequestBudgetExhausted):
            client.get("/filings/")


class TestTheSweepOrderRotates:
    def test_every_company_is_still_in_the_sweep(self):
        universe = ["A", "B", "C", "D", "E"]
        for day in range(12):
            assert sorted(sweep_order(universe, day)) == universe

    def test_the_starting_company_moves_from_day_to_day(self):
        universe = ["A", "B", "C", "D", "E"]
        starts = {sweep_order(universe, day)[0] for day in range(len(universe))}
        assert starts == set(universe), "a truncated sweep must not always drop the same tail"

    def test_an_empty_universe_is_empty(self):
        assert sweep_order([], 7) == []
