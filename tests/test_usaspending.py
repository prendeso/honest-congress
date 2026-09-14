"""Federal contract ingestion, pinned to a real USASpending response.

`tests/fixtures/usaspending/spending_by_transaction_2024.json` is the first
page of the twelve largest 2024 contract actions, captured verbatim. It is a
good fixture precisely because it is unedited: it happens to contain the
subsidiary case (Electric Boat, Bath Iron Works, Humana Government Business),
the untradeable case (Triad National Security, Savannah River Nuclear
Solutions), and the same recipient appearing twice under two spellings
("NORTHROP GRUMMAN SYSTEMS CORP" and "... CORPORATION").

Two things are worth protecting here.

The endpoint choice. `spending_by_award` returns the PARENT award with its
original base date, so a filter on 2024 hands back a Lockheed contract dated
1993-10-15. A detector asking "what was bought shortly before this award" cannot
use that. `spending_by_transaction` returns the individual award action with the
date it happened, and a test asserts the dates that reach the database are inside
the requested window.

The attribution. The ingester asks USASpending one question per company anyone in
Congress has traded, and `recipient_search_text` answers generously -- it matches
through USASpending's own recipient hierarchy, so a query for one company returns
awards to entities the SEC register does not tie back to it. The fake below is
deliberately the worst case: it hands back the ENTIRE fixture for every company
asked about. Everything that ends up correctly attributed is the round-trip guard
doing the work, and a test asserts each stored row's ticker is the one its own
recipient name resolves to.
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


@pytest.fixture
def over_matching_search(awards_payload):
    """A `fetch_awards` that returns everything, whoever is asked about.

    This is the shape of the real risk, exaggerated: `recipient_search_text`
    does not promise that what comes back belongs to the company in the query.
    """
    return patch(
        "src.ingestion.usaspending.fetch_awards",
        return_value=awards_payload["results"],
    )


# ---------------- the request ----------------


def test_fetch_uses_the_transaction_endpoint(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", pages=1, session=session)
    url, kwargs = session.post.call_args[0][0], session.post.call_args[1]
    assert url == SEARCH_URL
    assert url.endswith("spending_by_transaction/")
    assert "Action Date" in kwargs["json"]["fields"]


def test_fetch_restricts_the_search_to_one_recipient(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", recipient="NORTHROP GRUMMAN CORP", session=session)
    filters = session.post.call_args[1]["json"]["filters"]
    assert filters["recipient_search_text"] == ["NORTHROP GRUMMAN CORP"]


def test_fetch_without_a_recipient_searches_every_recipient(awards_payload):
    session = _http(awards_payload)
    fetch_awards("2024-01-01", "2024-12-31", session=session)
    assert "recipient_search_text" not in session.post.call_args[1]["json"]["filters"]


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


# ---------------- the universe ----------------


def test_no_traded_tickers_asks_usaspending_nothing(db_session, resolver):
    with patch("src.ingestion.usaspending.fetch_awards") as fetch:
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", resolver=resolver
        )

    assert fetch.call_count == 0
    assert result["tickers_queried"] == 0
    assert result["imported"] == 0


def test_a_ticker_with_no_registered_name_costs_no_request(db_session, resolver):
    with patch("src.ingestion.usaspending.fetch_awards") as fetch:
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["ZZZZ"], resolver=resolver
        )

    assert result["tickers_without_a_registered_name"] == 1
    assert result["tickers_queried"] == 0
    assert fetch.call_count == 0


def test_one_request_per_traded_company_named_from_the_sec_register(
    db_session, resolver, over_matching_search
):
    with over_matching_search as fetch:
        ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["GD", "BA"], resolver=resolver
        )

    assert fetch.call_count == 2
    asked = {call.kwargs["recipient"] for call in fetch.call_args_list}
    assert {resolver.name_for("GD"), resolver.name_for("BA")} == asked


# ---------------- the attribution ----------------


def test_a_company_query_keeps_only_that_companys_awards(
    db_session, resolver, over_matching_search
):
    # The fake hands back Lockheed, Boeing, Northrop and a national laboratory
    # alongside the two General Dynamics yards. Only the yards may be stored.
    with over_matching_search:
        ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["GD"], resolver=resolver
        )

    rows = db_session.query(GovernmentContract).all()
    assert rows
    assert {r.ticker for r in rows} == {"GD"}


def test_every_stored_award_resolves_to_the_ticker_it_is_filed_under(
    db_session, resolver, over_matching_search, awards_payload
):
    with over_matching_search:
        ingest_government_contracts(
            db_session,
            "2024-01-01",
            "2024-12-31",
            tickers=["GD", "BA", "NOC", "LMT", "HUM"],
            resolver=resolver,
        )

    rows = db_session.query(GovernmentContract).all()
    # Twelve award actions, less the three run by national laboratory operators
    # that resolve to no ticker at all.
    assert len(rows) == 9
    assert {r.ticker for r in rows} == {"GD", "BA", "NOC", "LMT", "HUM"}

    # Every stored award sits under the ticker its OWN recipient name resolves
    # to, not under whichever company query happened to return it.
    by_action = {
        str(a["internal_id"]): resolver.resolve(a["Recipient Name"])
        for a in awards_payload["results"]
    }
    for row in rows:
        assert row.ticker == by_action[row.external_id]


def test_subsidiaries_are_recorded_against_the_listed_parent(
    db_session, resolver, over_matching_search
):
    with over_matching_search:
        ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["GD", "HUM", "NOC"], resolver=resolver
        )

    tickers = {r.ticker for r in db_session.query(GovernmentContract).all()}
    # Electric Boat and Bath Iron Works are both General Dynamics; without the
    # overrides these two awards are invisible to a ticker-keyed detector.
    assert "GD" in tickers
    assert "HUM" in tickers  # Humana Government Business
    assert "NOC" in tickers  # "NORTHROP GRUMMAN SYSTEMS CORP"


def test_a_recipient_the_register_cannot_tie_back_is_rejected_and_named(
    db_session, resolver, over_matching_search
):
    # Sandia National Laboratories is operated for the Department of Energy by a
    # Honeywell subsidiary, so USASpending's hierarchy will hand it back under a
    # parent this fixture's register does not connect it to. It is rejected --
    # and counted BY NAME, because a silent rejection is how a coverage gap
    # stays invisible. Every real gap measured against the live API looks like
    # this: "CACI, INC. - FEDERAL", "DELL FEDERAL SYSTEMS L.P", "CHEVRON USA
    # INC." Naming them is what lets someone write the assertion down.
    with over_matching_search:
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
        )

    assert result["imported"] == 2  # the two Lockheed actions, and only those
    assert result["rejected_wrong_company"] == 10
    assert any("SANDIA" in name.upper() for name in result["rejected_names"])
    assert sum(result["rejected_names"].values()) == result["rejected_wrong_company"]


def test_untradeable_recipients_are_skipped_not_guessed(db_session, resolver, over_matching_search):
    with over_matching_search:
        ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
        )

    # Triad National Security and Savannah River Nuclear Solutions run national
    # laboratories. There is no security to trade, so skipping them is correct.
    descriptions = {r.description for r in db_session.query(GovernmentContract).all()}
    assert not any("TRIAD" in (d or "").upper() for d in descriptions)


def test_every_fetched_action_is_accounted_for(db_session, resolver, over_matching_search):
    with over_matching_search:
        result = ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["GD", "BA"], resolver=resolver
        )

    assert (
        result["imported"] + result["rejected_wrong_company"] + result["duplicates"]
        == result["fetched"]
    )


# ---------------- dates, reruns, bounds ----------------


def test_awarded_date_is_the_action_date_not_a_parent_base_date(
    db_session, resolver, over_matching_search
):
    with over_matching_search:
        ingest_government_contracts(
            db_session, "2024-01-01", "2024-12-31", tickers=["LMT", "BA"], resolver=resolver
        )

    dates = [r.awarded_date for r in db_session.query(GovernmentContract).all()]
    assert dates and all(d is not None for d in dates)
    assert all(datetime(2024, 1, 1) <= d <= datetime(2024, 12, 31) for d in dates)


def test_rerunning_does_not_duplicate(db_session, resolver, awards_payload):
    for _ in range(2):
        with patch(
            "src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]
        ):
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2024-12-31", tickers=["LMT", "BA"], resolver=resolver
            )

    assert result["imported"] == 0
    assert result["duplicates"] > 0


def test_start_date_is_clamped_to_what_the_api_supports(db_session, resolver, awards_payload):
    # The award search has no data before 2007-10-01; asking anyway returns an
    # error rather than an empty result, so the clamp happens client-side.
    with patch(
        "src.ingestion.usaspending.fetch_awards", return_value=awards_payload["results"]
    ) as fetch:
        ingest_government_contracts(
            db_session, "1999-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
        )
    assert fetch.call_args[0][0] == EARLIEST_SEARCH_DATE
