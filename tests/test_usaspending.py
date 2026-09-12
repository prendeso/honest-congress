"""Federal contract ingestion, pinned to a real USASpending response.

`tests/fixtures/usaspending/spending_by_transaction_2024.json` is the first
page of the twelve largest 2024 contract actions, captured verbatim. It is a
good fixture precisely because it is unedited: it happens to contain the
subsidiary case (Electric Boat, Bath Iron Works, Humana Government Business),
the untradeable case (Triad National Security, Savannah River Nuclear
Solutions), and the same recipient appearing twice under two spellings
("NORTHROP GRUMMAN SYSTEMS CORP" and "... CORPORATION").

The endpoint choice is the thing most worth protecting here. `spending_by_award`
returns the PARENT award with its original base date, so a filter on 2024 hands
back a Lockheed contract dated 1993-10-15. A detector asking "what was bought
shortly before this award" cannot use that. `spending_by_transaction` returns
the individual award action with the date it happened, and a test asserts the
dates that reach the database are inside the requested window.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.db.models import GovernmentContract
from src.ingestion.sec_tickers import TickerResolver
from src.ingestion.usaspending import (
    EARLIEST_SEARCH_DATE,
    SEARCH_URL,
    fetch_awards,
    ingest_government_contracts,
)

AWARDS = Path(__file__).parent / "fixtures" / "usaspending" / "spending_by_transaction_2024.json"
SEC = Path(__file__).parent / "fixtures" / "sec" / "company_tickers.json"


def _http(payload) -> MagicMock:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    session.post.return_value = response
    session.get.return_value = response
    return session


@pytest.fixture
def resolver() -> TickerResolver:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = json.loads(SEC.read_text())
    response.raise_for_status.return_value = None
    session.get.return_value = response
    return TickerResolver(session=session)


@pytest.fixture
def awards_payload() -> dict:
    return json.loads(AWARDS.read_text())


def test_fetch_uses_the_transaction_endpoint(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", pages=1, session=session)
    url, kwargs = session.post.call_args[0][0], session.post.call_args[1]
    assert url == SEARCH_URL
    assert url.endswith("spending_by_transaction/")
    assert "Action Date" in kwargs["json"]["fields"]


def test_fetch_stops_when_the_page_is_the_last(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", pages=5, session=session)
    assert session.post.call_count == 1


def test_fetch_asks_for_the_largest_awards_first(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", pages=1, session=session)
    body = session.post.call_args[1]["json"]
    assert body["sort"] == "Transaction Amount"
    assert body["order"] == "desc"


def test_ingest_imports_resolvable_recipients(db_session, awards_payload, resolver):
    with patch("src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]):
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", resolver=resolver
        )

    rows = db_session.query(GovernmentContract).all()
    assert result["imported"] == len(rows)
    assert result["fetched"] == len(awards_payload["results"])
    assert result["imported"] + result["unresolved_recipients"] + result["duplicates"] == len(
        awards_payload["results"]
    )
    assert {r.source for r in rows} == {"usaspending"}


def test_subsidiaries_are_recorded_against_the_listed_parent(db_session, awards_payload, resolver):
    with patch("src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]):
        ingest_government_contracts(db_session, "2024-01-01", "2024-12-31", resolver=resolver)

    tickers = {r.ticker for r in db_session.query(GovernmentContract).all()}
    # Electric Boat and Bath Iron Works are both General Dynamics; without the
    # overrides these two awards are invisible to a ticker-keyed detector.
    assert "GD" in tickers
    assert "HUM" in tickers  # Humana Government Business
    assert "NOC" in tickers  # "NORTHROP GRUMMAN SYSTEMS CORP"


def test_untradeable_recipients_are_skipped_not_guessed(db_session, awards_payload, resolver):
    with patch("src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]):
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", resolver=resolver
        )

    # Triad National Security and Savannah River Nuclear Solutions run national
    # laboratories. There is no security to trade, so skipping them is correct.
    assert result["unresolved_recipients"] > 0
    descriptions = {r.description for r in db_session.query(GovernmentContract).all()}
    assert not any("TRIAD" in (d or "").upper() for d in descriptions)


def test_awarded_date_is_the_action_date_not_a_parent_base_date(
    db_session, awards_payload, resolver
):
    with patch("src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]):
        ingest_government_contracts(db_session, "2024-01-01", "2024-12-31", resolver=resolver)

    dates = [r.awarded_date for r in db_session.query(GovernmentContract).all()]
    assert dates and all(d is not None for d in dates)
    assert all(datetime(2024, 1, 1) <= d <= datetime(2024, 12, 31) for d in dates)


def test_rerunning_does_not_duplicate(db_session, awards_payload, resolver):
    for _ in range(2):
        with patch(
            "src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]
        ):
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2024-12-31", resolver=resolver
            )

    assert result["imported"] == 0
    assert result["duplicates"] > 0


def test_start_date_is_clamped_to_what_the_api_supports(db_session, awards_payload, resolver):
    # The award search has no data before 2007-10-01; asking anyway returns an
    # error rather than an empty result, so the clamp happens client-side.
    with patch(
        "src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]
    ) as fetch:
        ingest_government_contracts(db_session, "1999-01-01", "2024-12-31", resolver=resolver)
    assert fetch.call_args[0][0] == EARLIEST_SEARCH_DATE
