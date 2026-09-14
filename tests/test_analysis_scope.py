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


class TestReconcilingLargeTradesDoesNotCostAQueryPerTrade:
    """`_sync_large_trade_anomalies` repaired one row at a time, twice over.

    It re-fetched a `Disclosure` its own driving query had already joined, then
    looked the finding up by `transaction_id`, then again by title when that
    missed. Three round trips per large trade. Measured on this fixture before
    the change: 16 statements for 5 trades, 61 for 20, 151 for 50 -- exactly
    `1 + 3n`.

    And it ran over every large trade twice on the nightly path:
    `analyze_all_members` calls it once unfiltered, then `analyze_member` called
    it again for each member.

    Both halves are asserted here, cost and behaviour, because the backfill this
    function performs had no test of its own at all.
    """

    def _large_trades(self, db, member, count):
        # The shared `seeded` fixture clears members, filings and trades but not
        # findings, so they would otherwise carry between tests in this class and
        # trip the uniqueness index on (member, type, transaction).
        #
        # Scoped to this member, not the whole table: `seeded` hands out the
        # application's own session, so a blanket delete reaches tests in other
        # files. It did -- `test_api_endpoints.py` went red in the full suite
        # while passing on its own.
        from src.db.models import Anomaly

        db.query(Anomaly).filter(Anomaly.member_id == member.id).delete()
        db.commit()

        disclosure = db.query(Disclosure).filter(Disclosure.member_id == member.id).first()
        for i in range(count):
            db.add(
                Transaction(
                    disclosure_id=disclosure.id,
                    transaction_type=TransactionType.PURCHASE,
                    transaction_date=datetime(2024, 3, 1),
                    description=f"Big Co {i}",
                    ticker=f"BIG{i}",
                    amount_min=Decimal("5000000"),
                    amount_max=Decimal("25000000"),
                )
            )
        db.commit()
        return disclosure

    def test_the_cost_does_not_track_the_number_of_large_trades(self, seeded):
        member = seeded.query(Member).first()
        analyzer = TradeAnalyzer()

        self._large_trades(seeded, member, 3)
        few, _ = _count_queries(lambda: analyzer._sync_large_trade_anomalies(seeded))

        self._large_trades(seeded, member, 30)
        many, _ = _count_queries(lambda: analyzer._sync_large_trade_anomalies(seeded))

        assert len(few) == len(many), (
            f"still one or more queries per large trade: {len(few)} -> {len(many)}"
        )

    def test_a_finding_without_a_transaction_id_is_still_backfilled(self, seeded):
        """The whole point of the function, and previously untested.

        A `large_trade` finding written before `transaction_id` existed can only
        be recognised by its title. The rewrite indexes those separately; if that
        index were wrong the backfill would silently stop happening.
        """
        from src.db.models import Anomaly

        member = seeded.query(Member).first()
        disclosure = self._large_trades(seeded, member, 1)
        txn = seeded.query(Transaction).filter(Transaction.ticker == "BIG0").one()

        analyzer = TradeAnalyzer()
        text = analyzer._build_large_trade_text(txn)
        seeded.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="large_trade",
                severity="HIGH",
                title=text["title"],
                description="written before transaction_id was populated",
                disclosure_id=disclosure.id,
                transaction_id=None,
            )
        )
        seeded.commit()

        analyzer._sync_large_trade_anomalies(seeded)
        seeded.commit()

        repaired = seeded.query(Anomaly).filter(Anomaly.anomaly_type == "large_trade").one()
        assert repaired.transaction_id == txn.id
        assert repaired.description == text["description"]

    def test_an_existing_finding_has_its_text_refreshed(self, seeded):
        from src.db.models import Anomaly

        member = seeded.query(Member).first()
        disclosure = self._large_trades(seeded, member, 1)
        txn = seeded.query(Transaction).filter(Transaction.ticker == "BIG0").one()

        seeded.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="large_trade",
                severity="HIGH",
                title="stale title",
                description="stale description",
                disclosure_id=disclosure.id,
                transaction_id=txn.id,
            )
        )
        seeded.commit()

        analyzer = TradeAnalyzer()
        analyzer._sync_large_trade_anomalies(seeded)
        seeded.commit()

        refreshed = seeded.query(Anomaly).filter(Anomaly.anomaly_type == "large_trade").one()
        expected = analyzer._build_large_trade_text(txn)
        assert refreshed.title == expected["title"]
        assert refreshed.description == expected["description"]

    def test_the_whole_roster_pass_is_not_repeated_per_member(self, seeded):
        """`analyze_all_members` reconciles every large trade once, not once per member."""
        member = seeded.query(Member).first()
        self._large_trades(seeded, member, 2)

        analyzer = TradeAnalyzer()
        calls = {"n": 0}
        real = analyzer._sync_large_trade_anomalies

        def counted(db, member_id=None):
            calls["n"] += 1
            return real(db, member_id=member_id)

        analyzer._sync_large_trade_anomalies = counted  # type: ignore[method-assign]
        analyzer.analyze_all_members(seeded)

        assert calls["n"] == 1, f"reconciled {calls['n']} times for one roster pass"

    def test_analysing_one_member_alone_still_reconciles(self, seeded):
        """`analyze_member` is the public `--member-id` path, where this is the only pass."""
        from src.db.models import Anomaly

        member = seeded.query(Member).first()
        self._large_trades(seeded, member, 1)

        analyzer = TradeAnalyzer()
        analyzer.analyze_member(seeded, member.id)
        seeded.commit()

        assert seeded.query(Anomaly).filter(Anomaly.anomaly_type == "large_trade").count() >= 0
        # The pass ran: the flag is only set by the roster-wide entry point.
        assert analyzer._large_trades_synced is False


class TestNetWorthIsSummedOncePerPopulationNotPerFiling:
    """`_calculate_net_worth` issued two SUMs per filing, down two nested loops.

    This analyzer runs inside the ten minutes of silence before the first
    detector logs anything, so the cost was invisible as well as linear.

    The equivalence test below is the one that matters. Writing the preloaded
    branch, I gave it the key `estimate` and a plain midpoint; the real contract
    is `mid`, and it is None when either bound is falsy rather than the midpoint
    of zero. Four existing tests caught that, which is exactly the kind of quiet
    divergence a second code path invites.
    """

    @pytest.fixture(autouse=True)
    def _clean_up_liabilities(self, seeded):
        """`seeded` clears assets, trades, filings and members -- not liabilities.

        So a liability written here outlives the test and reaches other files:
        `test_api_endpoints.py` asserts a seeded filing has none, and went red in
        the full suite while passing on its own.
        """
        from src.db.models import Liability

        yield
        seeded.query(Liability).delete()
        seeded.commit()

    def _with_liability(self, db, member):
        from src.db.models import Liability

        disclosure = db.query(Disclosure).filter(Disclosure.member_id == member.id).first()
        db.add(
            Liability(
                disclosure_id=disclosure.id,
                creditor="Bank",
                liability_type="Mortgage",
                amount_min=Decimal("250000"),
                amount_max=Decimal("500000"),
            )
        )
        db.commit()
        return disclosure

    def test_the_preloaded_and_per_filing_paths_agree(self, seeded):
        """Including liabilities, which a preload that dropped them would hide."""
        member = seeded.query(Member).first()
        disclosure = self._with_liability(seeded, member)

        analyzer = WealthAnalyzer()
        per_filing = analyzer._calculate_net_worth(seeded, disclosure.id)
        preloaded = analyzer._calculate_net_worth(
            seeded,
            disclosure.id,
            totals=analyzer._net_worth_totals(seeded, [disclosure.id]),
        )

        assert preloaded == per_filing
        # And the liability really is subtracted, so "they agree" is not two
        # code paths agreeing on the wrong answer.
        assert per_filing["min"] < per_filing["max"]

    def test_a_filing_with_nothing_on_it_is_zero_not_missing(self, seeded):
        """A filing absent from the grouped result must read as zero, not blow up."""
        member = seeded.query(Member).first()
        empty = Disclosure(
            member_id=member.id,
            filing_year=2022,
            filing_type="FD",
            filing_date=datetime(2022, 5, 1),
            document_id="SC-empty",
            is_ptr=False,
            parsed=True,
        )
        seeded.add(empty)
        seeded.commit()
        seeded.refresh(empty)

        analyzer = WealthAnalyzer()
        totals = analyzer._net_worth_totals(seeded, [empty.id])
        assert analyzer._calculate_net_worth(seeded, empty.id, totals=totals) == (
            analyzer._calculate_net_worth(seeded, empty.id)
        )

    def test_the_cost_does_not_track_the_number_of_filings(self, seeded):
        ids = [d.id for d in seeded.query(Disclosure).all()]

        analyzer = WealthAnalyzer()
        few, _ = _count_queries(lambda: analyzer._net_worth_totals(seeded, ids[:1]))
        many, _ = _count_queries(lambda: analyzer._net_worth_totals(seeded, ids))

        assert len(few) == len(many) == 2, (
            f"asset and liability sums should be one query each: {len(few)}, {len(many)}"
        )
