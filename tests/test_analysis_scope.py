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
