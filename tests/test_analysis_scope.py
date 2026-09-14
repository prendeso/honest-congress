"""The analysis step must not cost a query per name in the roster.

The roster imported from Congress.gov is every member in history -- 12,770
rows -- while the filings currently loaded belong to a few hundred of them.
Both analyzers walked the whole roster and called `analyze_member` on each,
which issues two queries before discovering the member has no data and
returning [].

That is ~25,000 round trips to a hosted database to produce nothing, and it
made `analyze` the longest step in the rebuild by a wide margin: the first run
over a populated database was still in that step more than two hours in, with
the 350-minute runner cap in sight.

Scoping is behaviour-preserving by construction -- the members dropped are
exactly the ones whose analysis returned [] -- so these tests assert both
halves: the cost stops tracking the roster, AND the findings do not change.

Queries are counted rather than timed. A timing assertion is flaky and does not
say what broke; "it issued one query per member" is the actual defect.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import TradeAnalyzer
from src.analysis.wealth_analyzer import WealthAnalyzer
from src.db.models import (
    Asset,
    AssetType,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


def _count_queries(callable_):
    from sqlalchemy import event

    from src.db import engine

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = callable_()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen, result


@pytest.fixture
def seeded():
    """One member who actually filed and traded, on a clean database."""
    from src.db import SessionLocal

    db = SessionLocal()
    for table in (Asset, Transaction, Disclosure, Member):
        db.query(table).delete()
    db.commit()

    member = Member(
        bioguide_id="SC00001",
        first_name="Scope",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="WA",
        in_office=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    for year in (2023, 2024):
        disclosure = Disclosure(
            member_id=member.id,
            filing_year=year,
            filing_type="PTR",
            filing_date=datetime(year, 5, 1),
            document_id=f"SC-{year}",
            is_ptr=True,
            parsed=True,
            parse_confidence=1.0,
            has_text_layer=True,
        )
        db.add(disclosure)
        db.commit()
        db.refresh(disclosure)

        db.add(
            Asset(
                disclosure_id=disclosure.id,
                asset_type=AssetType.STOCK,
                description="Apple Inc",
                ticker="AAPL",
                value_min=Decimal("1000000" if year == 2024 else "1001"),
                value_max=Decimal("5000000" if year == 2024 else "15000"),
            )
        )
        db.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_type=TransactionType.PURCHASE,
                transaction_date=datetime(year, 3, 1),
                description="Apple Inc",
                ticker="AAPL",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
            )
        )
        db.commit()

    yield db
    db.close()


def _add_members_with_no_filings(db, count):
    for i in range(count):
        db.add(
            Member(
                bioguide_id=f"Z{i:06d}",
                first_name="Empty",
                last_name=f"Roster{i}",
                chamber=Chamber.HOUSE,
                party=Party.REPUBLICAN,
                state="TX",
            )
        )
    db.commit()


class TestTheCostDoesNotTrackTheRoster:
    def test_trade_analysis(self, seeded):
        before, _ = _count_queries(lambda: TradeAnalyzer().analyze_all_members(seeded))
        _add_members_with_no_filings(seeded, 60)
        after, _ = _count_queries(lambda: TradeAnalyzer().analyze_all_members(seeded))

        assert len(after) <= len(before) + 1, (
            f"adding 60 members with no filings added {len(after) - len(before)} "
            "queries -- the analysis is walking the roster again"
        )

    def test_wealth_analysis(self, seeded):
        before, _ = _count_queries(lambda: WealthAnalyzer().analyze_all_members(seeded))
        _add_members_with_no_filings(seeded, 60)
        after, _ = _count_queries(lambda: WealthAnalyzer().analyze_all_members(seeded))

        assert len(after) <= len(before) + 1, (
            f"adding 60 members with no filings added {len(after) - len(before)} "
            "queries -- the analysis is walking the roster again"
        )


class TestTheFindingsAreUnchanged:
    """The half that makes the optimisation safe rather than merely fast."""

    def test_trade_findings_survive_the_scoping(self, seeded):
        _, before = _count_queries(lambda: TradeAnalyzer().analyze_all_members(seeded))
        _add_members_with_no_filings(seeded, 60)
        _, after = _count_queries(lambda: TradeAnalyzer().analyze_all_members(seeded))

        assert after["total_anomalies"] == before["total_anomalies"]
        assert after["members_with_anomalies"] == before["members_with_anomalies"]

    def test_wealth_findings_survive_the_scoping(self, seeded):
        _, before = _count_queries(lambda: WealthAnalyzer().analyze_all_members(seeded))
        _add_members_with_no_filings(seeded, 60)
        _, after = _count_queries(lambda: WealthAnalyzer().analyze_all_members(seeded))

        assert after["total_anomalies"] == before["total_anomalies"]
        assert after["members_with_anomalies"] == before["members_with_anomalies"]

    def test_a_member_who_traded_is_still_analysed(self, seeded):
        """The guard against scoping down to nothing, which would make every
        test above pass on an analysis that does no work at all."""
        _, result = _count_queries(lambda: TradeAnalyzer().analyze_all_members(seeded))

        assert result["members_analyzed"] == 1, result

    def test_a_member_with_two_filings_is_still_analysed(self, seeded):
        _, result = _count_queries(lambda: WealthAnalyzer().analyze_all_members(seeded))

        assert result["members_analyzed"] == 1, result


class TestTheDetectorsStopTrackingTheRosterToo:
    """The same defect, three detectors further on.

    `TradeAnalyzer` and `WealthAnalyzer` were scoped after `analyze` was found
    still running two hours in. The advanced and extended detectors were not,
    and one production run shows what that costs:

        1. Detecting wealth vs salary anomalies...    17m14s  ->  0 findings
        2. Detecting rapid asset appreciation...      17m14s  ->  0 findings
        1. Detecting trade timing anomalies...        17m16s  -> 108 findings

    Fifty-one minutes of a 168-minute step, almost all of it spent issuing a
    query per member to learn that the member has nothing to analyse. Each loop
    opens with an explicit `continue` -- fewer than two FD filings, or no trades
    -- so the scoping asks for exactly the members that got past it.
    """

    def _detect(self, name):
        from src.analysis import AdvancedAnomalyDetector, ExtendedAnomalyDetector

        if name == "trade_timing":
            return lambda db: ExtendedAnomalyDetector().detect_trade_timing_anomalies(db)
        detector = AdvancedAnomalyDetector()
        return lambda db: getattr(detector, name)(db)

    @pytest.mark.parametrize(
        "detector",
        [
            "detect_wealth_vs_salary_anomalies",
            "detect_asset_appreciation_anomalies",
            "trade_timing",
        ],
    )
    def test_cost_does_not_grow_with_members_who_have_nothing(self, seeded, detector):
        run = self._detect(detector)

        before, _ = _count_queries(lambda: run(seeded))
        _add_members_with_no_filings(seeded, 50)
        after, _ = _count_queries(lambda: run(seeded))

        assert len(after) == len(before), (
            f"{detector} issued {len(after) - len(before)} extra queries for 50 "
            "members with no filings at all"
        )

    @pytest.mark.parametrize(
        "detector",
        [
            "detect_wealth_vs_salary_anomalies",
            "detect_asset_appreciation_anomalies",
            "trade_timing",
        ],
    )
    def test_the_findings_do_not_change(self, seeded, detector):
        run = self._detect(detector)

        _, before = _count_queries(lambda: run(seeded))
        _add_members_with_no_filings(seeded, 50)
        _, after = _count_queries(lambda: run(seeded))

        # Scoping is only defensible if it drops members whose output was empty.
        assert [a.get("title") for a in before] == [a.get("title") for a in after]

    def test_a_member_who_filed_once_is_not_asked_about_twice(self, seeded):
        """The FD loops need two filings to compare. One is not two."""
        from src.db.models import Disclosure as D

        member = Member(
            bioguide_id="SC00002",
            first_name="One",
            last_name="Filing",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="OR",
        )
        seeded.add(member)
        seeded.commit()
        seeded.refresh(member)
        seeded.add(
            D(
                member_id=member.id,
                filing_year=2024,
                filing_type="FD",
                filing_date=datetime(2024, 5, 1),
                document_id="SC-ONE",
                is_ptr=False,
                parsed=True,
            )
        )
        seeded.commit()

        run = self._detect("detect_wealth_vs_salary_anomalies")
        seen, _ = _count_queries(lambda: run(seeded))

        assert not any(f"member_id = {member.id}" in s for s in seen)
