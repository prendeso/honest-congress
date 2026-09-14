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
    award_action_key,
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
        award_action_key(a): resolver.resolve(a["Recipient Name"])
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


class TestDeobligationsAreStoredButCounted:
    """An action that takes money back is real, and it is not an award.

    Asking USASpending for Lockheed Martin since 2023 ascending returns an
    action at -$1,882,437,667 -- the Navy releasing money it no longer owed.
    The row belongs in the table (it happened, and the table is the record of
    the feed), but the ingest has to say how many of these it stored, because
    the detector will silently not count them and a silent difference between
    "imported" and "treated as awards" is how a number goes unexplained.
    """

    def _awards(self, amounts):
        return [
            {
                "Award ID": f"W9124{i}",
                "Recipient Name": "GENERAL DYNAMICS CORP",
                "Transaction Amount": amount,
                "Action Date": f"2024-03-0{i + 1}",
                "Awarding Agency": "Department of Defense",
                "Transaction Description": "TEST",
                "Mod": str(i),
            }
            for i, amount in enumerate(amounts)
        ]

    def _ingest(self, db, resolver, amounts):
        with patch("src.ingestion.usaspending.fetch_awards", return_value=self._awards(amounts)):
            return ingest_government_contracts(
                db, "2024-01-01", "2024-12-31", tickers=["GD"], resolver=resolver
            )

    def test_the_negative_action_is_stored(self, db_session, resolver):
        result = self._ingest(db_session, resolver, [5_000_000, -1_882_437_667.02])
        assert result["imported"] == 2
        stored = {c.amount for c in db_session.query(GovernmentContract).all()}
        assert any(a is not None and a < 0 for a in stored)

    def test_deobligations_and_zero_dollar_mods_are_counted(self, db_session, resolver):
        result = self._ingest(db_session, resolver, [5_000_000, -1_882_437_667.02, 0])
        assert result["imported"] == 3
        assert result["money_taken_back"] == 2

    def test_a_feed_of_real_awards_reports_none(self, db_session, resolver):
        result = self._ingest(db_session, resolver, [5_000_000, 12_000])
        assert result["money_taken_back"] == 0


# ---------------- one award is not one action ----------------


class TestTheKeyIsTheActionNotTheAward:
    """USASpending's `internal_id` identifies the CONTRACT, not the obligation.

    Contract W31P4Q24C0022 came back from the live API as nine transaction rows
    sharing one `internal_id`, with six distinct action dates spanning
    2024-06-28 to 2026-03-06 and nine distinct amounts. Over 500 live rows the
    id had 437 distinct values, so deduplicating on it threw away 63 real award
    actions -- and what the discarded ones differ in is the ACTION DATE, the one
    field `detect_contract_front_runs` reads. A purchase before the March 2026
    obligation could not be flagged, because only September 2025 was stored.

    `generated_internal_id` is the same value in a readable spelling and has
    exactly the same problem.
    """

    # Trimmed from the live response for W31P4Q24C0022. Same recipient, same
    # contract, same internal_id; different obligations.
    NINE_OBLIGATIONS = [
        {
            "internal_id": 348950884,
            "Award ID": "W31P4Q24C0022",
            "Recipient Name": "LOCKHEED MARTIN CORPORATION",
            "Action Date": date,
            "Transaction Amount": amount,
            "Awarding Agency": "Department of Defense",
            "Transaction Description": "MISSILE PRODUCTION",
        }
        for date, amount in (
            ("2025-09-29", 1876405331.29),
            ("2026-03-06", 1479881421.64),
            ("2024-06-28", 1361764787.87),
            ("2025-08-29", 1030244056.17),
            ("2024-06-28", 854653680.58),
            ("2025-08-29", 847998635.12),
            ("2025-09-29", 820035743.42),
            ("2026-03-06", 703559640.0),
            ("2024-09-25", 390252562.58),
        )
    ]

    def test_the_award_id_alone_does_not_identify_an_obligation(self):
        assert len({str(a["internal_id"]) for a in self.NINE_OBLIGATIONS}) == 1
        assert len({award_action_key(a) for a in self.NINE_OBLIGATIONS}) == 9

    def test_all_nine_obligations_are_stored(self, db_session, resolver):
        with patch("src.ingestion.usaspending.fetch_awards", return_value=self.NINE_OBLIGATIONS):
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2026-12-31", tickers=["LMT"], resolver=resolver
            )

        assert result["imported"] == 9
        assert result["duplicates"] == 0
        rows = db_session.query(GovernmentContract).all()
        # The dates are what the detector reads, and all six must survive.
        assert {r.awarded_date.strftime("%Y-%m-%d") for r in rows} == {
            "2024-06-28",
            "2024-09-25",
            "2025-08-29",
            "2025-09-29",
            "2026-03-06",
        }

    def test_two_actions_differing_only_by_modification_both_survive(self, db_session, resolver):
        # The case the old comment was right to worry about and the old key did
        # not actually cover: one contract, one day, one amount, two mods.
        same_day = [
            {
                "internal_id": 1,
                "Award ID": "W31P4Q24C0022",
                "Mod": mod,
                "Recipient Name": "LOCKHEED MARTIN CORPORATION",
                "Action Date": "2024-06-28",
                "Transaction Amount": 500000.0,
                "Awarding Agency": "Department of Defense",
                "Transaction Description": "MISSILE PRODUCTION",
            }
            for mod in ("P00001", "P00002")
        ]
        with patch("src.ingestion.usaspending.fetch_awards", return_value=same_day):
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
            )
        assert result["imported"] == 2

    def test_the_identical_row_repeated_is_still_one_row(self, db_session, resolver):
        repeat = [dict(self.NINE_OBLIGATIONS[0]) for _ in range(4)]
        with patch("src.ingestion.usaspending.fetch_awards", return_value=repeat):
            result = ingest_government_contracts(
                db_session, "2024-01-01", "2026-12-31", tickers=["LMT"], resolver=resolver
            )
        assert result["imported"] == 1
        assert result["duplicates"] == 3

    def test_the_request_asks_for_the_modification_number(self, awards_payload):
        session = _http(awards_payload)
        fetch_awards("2024-01-01", "2024-12-31", session=session)
        assert "Mod" in session.post.call_args[1]["json"]["fields"]

    def test_an_award_with_no_contract_number_is_not_given_a_key(self):
        assert award_action_key({"Action Date": "2024-01-01", "Transaction Amount": 1}) is None


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


# ---------------- cost ----------------


def _count_queries(engine, callable_):
    from sqlalchemy import event

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        callable_()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


class TestCostDoesNotTrackTheNumberOfAwards:
    """Asking "is this one already stored?" once per award is an N+1.

    It was affordable while the feed took three hundred awards in total. Asking
    per traded company returns tens of thousands, `ingest-contracts` runs on a
    GitHub runner against a hosted database, and on a nightly rerun EVERY award
    is already present -- so the entire cost would be paid to discover there is
    nothing to do.

    The keys already stored are read once instead, which is also the within-run
    duplicate guard: `SessionLocal` is autoflush=False, so a row added earlier
    in the same batch is invisible to a query and had to be tracked in memory
    regardless.
    """

    def _awards(self, n: int):
        return [
            {
                "Award ID": f"W31P4Q24C{i:04d}",
                "Mod": "0",
                "Recipient Name": "LOCKHEED MARTIN CORPORATION",
                "Action Date": "2024-06-28",
                "Transaction Amount": 1000.0 + i,
                "Awarding Agency": "Department of Defense",
                "Transaction Description": f"Award {i}",
            }
            for i in range(n)
        ]

    def test_one_award_and_thirty_cost_the_same_to_look_up(self, db_session, engine, resolver):
        # INSERTs scale with rows and must; what must not is the number of
        # questions asked before deciding whether a row is new.
        def selects(n: int) -> list[str]:
            with patch("src.ingestion.usaspending.fetch_awards", return_value=self._awards(n)):
                statements = _count_queries(
                    engine,
                    lambda: ingest_government_contracts(
                        db_session, "2024-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
                    ),
                )
            return [s for s in statements if s.lstrip().upper().startswith("SELECT")]

        one = selects(1)
        thirty = selects(30)

        assert len(one) == len(thirty) == 1, (
            f"lookups grew with the number of awards: {len(one)} -> {len(thirty)}. "
            "That is one round trip per award to a hosted database."
        )

    def test_a_rerun_over_stored_awards_still_asks_once(self, db_session, engine, resolver):
        for _ in range(2):
            with patch("src.ingestion.usaspending.fetch_awards", return_value=self._awards(30)):
                statements = _count_queries(
                    engine,
                    lambda: ingest_government_contracts(
                        db_session, "2024-01-01", "2024-12-31", tickers=["LMT"], resolver=resolver
                    ),
                )
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1, selects
