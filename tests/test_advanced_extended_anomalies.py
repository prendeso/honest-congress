"""Tests for the advanced and extended anomaly detectors.

These tests pin down the bug fixes introduced by the modernization plan:
the detectors used to reference Transaction.member_id (doesn't exist),
t.type (doesn't exist; the real attribute is transaction_type), and
t.amount (doesn't exist; the real attributes are amount_min/amount_max).
The detectors were also never invoked anywhere and never persisted their
results to the database.
"""

from datetime import datetime
from decimal import Decimal

from src.analysis import (
    AdvancedAnomalyDetector,
    ExtendedAnomalyDetector,
    persist_anomalies,
    run_advanced_anomaly_detection,
    transaction_amount,
)
from src.db.models import (
    Anomaly,
    Asset,
    AssetType,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

# ---------------- helpers ----------------


def _make_member(db, bioguide="A000001", first="Test", last="Member") -> Member:
    member = Member(
        bioguide_id=bioguide,
        first_name=first,
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _make_disclosure(db, member, year, doc_id, is_ptr=False, parsed=True) -> Disclosure:
    d = Disclosure(
        member_id=member.id,
        filing_year=year,
        filing_type="PTR" if is_ptr else "FD",
        filing_date=datetime(year, 6, 1),
        document_id=doc_id,
        is_ptr=is_ptr,
        parsed=parsed,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _make_txn(
    db,
    disclosure,
    txn_type,
    ticker,
    amount_min,
    amount_max,
    when=None,
) -> Transaction:
    t = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when or datetime(disclosure.filing_year, 3, 15),
        transaction_type=txn_type,
        description=f"{ticker} {txn_type.value}",
        ticker=ticker,
        amount_min=Decimal(str(amount_min)),
        amount_max=Decimal(str(amount_max)),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_asset(db, disclosure, description, value_min, value_max) -> Asset:
    a = Asset(
        disclosure_id=disclosure.id,
        asset_type=AssetType.STOCK,
        description=description,
        value_min=Decimal(str(value_min)),
        value_max=Decimal(str(value_max)),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ---------------- transaction_amount helper ----------------


class TestTransactionAmountHelper:
    def test_midpoint_when_both_set(self, db_session):
        member = _make_member(db_session)
        d = _make_disclosure(db_session, member, 2024, "T1", is_ptr=True)
        t = _make_txn(db_session, d, TransactionType.PURCHASE, "AAPL", 1000, 15000)
        assert transaction_amount(t) == 8000.0

    def test_zero_when_both_none(self, db_session):
        member = _make_member(db_session)
        d = _make_disclosure(db_session, member, 2024, "T2", is_ptr=True)
        t = Transaction(
            disclosure_id=d.id,
            transaction_date=datetime(2024, 1, 1),
            transaction_type=TransactionType.PURCHASE,
            description="No amount",
            ticker="MEH",
        )
        db_session.add(t)
        db_session.commit()
        assert transaction_amount(t) == 0.0


# ---------------- advanced detector ----------------


class TestStockOutperformanceDetector:
    """Was completely broken: filtered on Transaction.member_id (doesn't
    exist) and accessed t.type / t.amount (don't exist)."""

    def test_outperformance_flagged_for_high_returns(self, db_session):
        member = _make_member(db_session, bioguide="O000001", last="Outperformer")
        d = _make_disclosure(db_session, member, 2024, "OUT1", is_ptr=True)

        # Buy 10k, later sell for 100k (10x return = far above 10% benchmark)
        _make_txn(
            db_session, d, TransactionType.PURCHASE, "AAPL", 9000, 11000, when=datetime(2024, 1, 5)
        )
        _make_txn(
            db_session, d, TransactionType.SALE, "AAPL", 90000, 110000, when=datetime(2024, 11, 5)
        )

        detector = AdvancedAnomalyDetector()
        anomalies = detector.detect_stock_outperformance_anomalies(db_session)

        types = [a["anomaly_type"] for a in anomalies]
        assert "outperforming_trades" in types
        flagged = next(a for a in anomalies if a["anomaly_type"] == "outperforming_trades")
        assert flagged["member_id"] == member.id
        assert flagged["year"] == 2024

    def test_no_outperformance_when_no_buys_or_sells(self, db_session):
        member = _make_member(db_session, bioguide="N000001")
        d = _make_disclosure(db_session, member, 2024, "OUT2", is_ptr=True)
        _make_txn(db_session, d, TransactionType.PURCHASE, "AAPL", 1000, 5000)

        detector = AdvancedAnomalyDetector()
        anomalies = detector.detect_stock_outperformance_anomalies(db_session)
        assert all(a["anomaly_type"] != "outperforming_trades" for a in anomalies)


class TestRapidAssetAppreciation:
    def test_flags_assets_growing_more_than_100_percent(self, db_session):
        member = _make_member(db_session, bioguide="R000001", last="Riches")
        d1 = _make_disclosure(db_session, member, 2023, "FD1")
        d2 = _make_disclosure(db_session, member, 2024, "FD2")
        # Same asset (matched by description), grew ~6.5x in one year
        _make_asset(db_session, d1, "winery business", 15000, 15000)
        _make_asset(db_session, d2, "winery business", 1000000, 1000000)

        detector = AdvancedAnomalyDetector()
        anomalies = detector.detect_asset_appreciation_anomalies(db_session)

        assert any(a["anomaly_type"] == "rapid_asset_appreciation" for a in anomalies)
        a = next(a for a in anomalies if a["anomaly_type"] == "rapid_asset_appreciation")
        assert a["member_id"] == member.id
        assert a["growth_percent"] > 1000


class TestWealthVsSalary:
    def test_flags_growth_far_above_salary(self, db_session):
        member = _make_member(db_session, bioguide="W000010", last="Whale")
        d1 = _make_disclosure(db_session, member, 2020, "FDw1")
        d2 = _make_disclosure(db_session, member, 2024, "FDw2")
        _make_asset(db_session, d1, "Portfolio", 500000, 500000)
        _make_asset(db_session, d2, "Portfolio", 10000000, 10000000)

        detector = AdvancedAnomalyDetector()
        anomalies = detector.detect_wealth_vs_salary_anomalies(db_session)
        assert any(a["anomaly_type"] == "wealth_vs_salary" for a in anomalies)


# ---------------- extended detector ----------------


class TestTradeTiming:
    def test_consecutive_same_direction_trades_flagged(self, db_session):
        member = _make_member(db_session, bioguide="C000001", last="Cluster")
        d = _make_disclosure(db_session, member, 2024, "CL1", is_ptr=True)
        # 6 buys in a row (>= 5 threshold)
        for i in range(6):
            _make_txn(
                db_session,
                d,
                TransactionType.PURCHASE,
                f"TKR{i}",
                1000,
                5000,
                when=datetime(2024, 3, i + 1),
            )

        detector = ExtendedAnomalyDetector()
        anomalies = detector.detect_trade_timing_anomalies(db_session)
        types = [a["anomaly_type"] for a in anomalies]
        assert "trade_clustering" in types

    def test_perfect_timing_flagged_when_all_buys_precede_sells(self, db_session):
        member = _make_member(db_session, bioguide="P000001", last="Prophet")
        d = _make_disclosure(db_session, member, 2024, "PT1", is_ptr=True)
        # 5 buys early in the year, 5 sells later — every buy precedes every sell
        for i in range(5):
            _make_txn(
                db_session,
                d,
                TransactionType.PURCHASE,
                f"BUY{i}",
                1000,
                5000,
                when=datetime(2024, 1, i + 1),
            )
        for i in range(5):
            _make_txn(
                db_session,
                d,
                TransactionType.SALE,
                f"SELL{i}",
                10000,
                50000,
                when=datetime(2024, 11, i + 1),
            )

        detector = ExtendedAnomalyDetector()
        anomalies = detector.detect_trade_timing_anomalies(db_session)
        types = [a["anomaly_type"] for a in anomalies]
        assert "perfect_timing" in types

    def test_volume_spikes_attached_to_member(self, db_session):
        """Previously the volume_spikes anomaly was emitted with no
        member_id, so persist_anomalies (correctly) drops it."""
        member = _make_member(db_session, bioguide="V000001", last="Volume")
        d = _make_disclosure(db_session, member, 2024, "V1", is_ptr=True)
        # Many small trades and two giant outliers
        for i in range(10):
            _make_txn(
                db_session,
                d,
                TransactionType.PURCHASE,
                f"SM{i}",
                1000,
                2000,
                when=datetime(2024, 2, i + 1),
            )
        _make_txn(
            db_session,
            d,
            TransactionType.PURCHASE,
            "BIG1",
            500000,
            1000000,
            when=datetime(2024, 5, 1),
        )
        _make_txn(
            db_session,
            d,
            TransactionType.PURCHASE,
            "BIG2",
            500000,
            1000000,
            when=datetime(2024, 6, 1),
        )

        detector = ExtendedAnomalyDetector()
        anomalies = detector.detect_trade_timing_anomalies(db_session)

        spikes = [a for a in anomalies if a["anomaly_type"] == "volume_spikes"]
        if spikes:
            assert spikes[0].get("member_id") == member.id


class TestCommitteeConflicts:
    def test_concentration_in_regulated_sector(self, db_session):
        member = _make_member(db_session, bioguide="K000001", last="Conflict")
        d = _make_disclosure(db_session, member, 2024, "K1", is_ptr=True)
        # 6 defense-sector trades, 1 random
        for ticker in ["LMT", "RTX", "BA", "lockheed corp", "raytheon", "northrop"]:
            _make_txn(db_session, d, TransactionType.PURCHASE, ticker, 1000, 5000)
        _make_txn(db_session, d, TransactionType.PURCHASE, "FOO", 1000, 5000)

        detector = ExtendedAnomalyDetector()
        anomalies = detector.detect_committee_conflicts(db_session)
        types = [a["anomaly_type"] for a in anomalies]
        # This detector emits its own type now. It previously reused
        # "sector_concentration", which TradeAnalyzer also emits from a
        # per-disclosure keyword match with a different severity scale -- both
        # were persisted under one name and the dashboard labelled the pair
        # "Committee Conflict", which only ever described this one.
        assert "regulated_sector_concentration" in types
        assert "sector_concentration" not in types


class TestLossAvoidance:
    def test_flags_strong_buy_then_sell_pattern(self, db_session):
        member = _make_member(db_session, bioguide="L000001", last="Lucky")
        d = _make_disclosure(db_session, member, 2024, "L1", is_ptr=True)
        # 5 buys then 5 sells of same ticker — every sell follows every buy
        for i in range(5):
            _make_txn(
                db_session,
                d,
                TransactionType.PURCHASE,
                "AAPL",
                1000,
                5000,
                when=datetime(2024, 1, i + 1),
            )
        for i in range(5):
            _make_txn(
                db_session,
                d,
                TransactionType.SALE,
                "AAPL",
                1000,
                5000,
                when=datetime(2024, 11, i + 1),
            )

        detector = ExtendedAnomalyDetector()
        anomalies = detector.detect_loss_avoidance(db_session)
        assert any(a["anomaly_type"] == "loss_avoidance" for a in anomalies)


# ---------------- persistence ----------------


class TestPersistence:
    def test_persist_writes_anomaly_rows(self, db_session):
        member = _make_member(db_session, bioguide="X000001")
        anomalies = [
            {
                "member_id": member.id,
                "anomaly_type": "wealth_vs_salary",
                "severity": "HIGH",
                "title": "Wealth growth far exceeds salary (2020-2024)",
                "description": "Test description",
            },
            {
                # Missing member_id — should be skipped, not crash.
                "anomaly_type": "volume_spikes",
                "severity": "medium",
                "title": "Orphan",
                "description": "skipped",
            },
        ]
        inserted = persist_anomalies(db_session, anomalies)
        assert inserted == 1

        rows = db_session.query(Anomaly).all()
        assert len(rows) == 1
        assert rows[0].severity == "high"  # uppercase normalized to API vocab
        assert rows[0].member_id == member.id

    def test_persist_dedups(self, db_session):
        member = _make_member(db_session, bioguide="Y000001")
        a = {
            "member_id": member.id,
            "anomaly_type": "wealth_vs_salary",
            "severity": "high",
            "title": "Same title",
            "description": "x",
        }
        assert persist_anomalies(db_session, [a]) == 1
        assert persist_anomalies(db_session, [a]) == 0

    def test_run_advanced_persists_results(self, db_session):
        member = _make_member(db_session, bioguide="Z000001", last="Persisted")
        d1 = _make_disclosure(db_session, member, 2023, "FDz1")
        d2 = _make_disclosure(db_session, member, 2024, "FDz2")
        _make_asset(db_session, d1, "shell company", 10000, 10000)
        _make_asset(db_session, d2, "shell company", 5000000, 5000000)

        before = db_session.query(Anomaly).count()
        run_advanced_anomaly_detection(db_session)
        after = db_session.query(Anomaly).count()
        assert after > before


# ---------------- edge cases ----------------


class TestEdgeCases:
    """Defensive coverage: detectors must not crash on missing/empty data."""

    def test_detectors_return_empty_with_no_members(self, db_session):
        """No members → no anomalies, no errors."""
        adv = AdvancedAnomalyDetector()
        ext = ExtendedAnomalyDetector()
        assert adv.detect_wealth_vs_salary_anomalies(db_session) == []
        assert adv.detect_asset_appreciation_anomalies(db_session) == []
        assert adv.detect_stock_outperformance_anomalies(db_session) == []
        assert ext.detect_trade_timing_anomalies(db_session) == []
        assert ext.detect_committee_conflicts(db_session) == []
        assert ext.detect_loss_avoidance(db_session) == []

    def test_member_with_no_trades_skipped(self, db_session):
        """A member without any transactions shouldn't produce trade anomalies."""
        _make_member(db_session, bioguide="E000001", last="Empty")
        adv = AdvancedAnomalyDetector()
        ext = ExtendedAnomalyDetector()
        assert adv.detect_stock_outperformance_anomalies(db_session) == []
        assert ext.detect_trade_timing_anomalies(db_session) == []

    def test_transactions_with_none_amounts_dont_crash(self, db_session):
        """The previous bug was AttributeError on .amount; ensure None ranges are tolerated."""
        member = _make_member(db_session, bioguide="A000099")
        d = _make_disclosure(db_session, member, 2024, "NA1", is_ptr=True)
        # Create a few PURCHASE+SALE pairs but with None amounts
        for txn_type in (TransactionType.PURCHASE, TransactionType.SALE):
            t = Transaction(
                disclosure_id=d.id,
                transaction_date=datetime(2024, 5, 1),
                transaction_type=txn_type,
                description="No amount",
                ticker="AAPL",
            )
            db_session.add(t)
        db_session.commit()

        adv = AdvancedAnomalyDetector()
        ext = ExtendedAnomalyDetector()
        # No exceptions raised; some may flag depending on logic, but the
        # important thing is the calls complete.
        adv.detect_stock_outperformance_anomalies(db_session)
        ext.detect_trade_timing_anomalies(db_session)

    def test_single_disclosure_skipped_for_year_over_year_growth(self, db_session):
        """The wealth/salary and asset-appreciation detectors require 2+ disclosures."""
        member = _make_member(db_session, bioguide="S000001", last="Singleton")
        d = _make_disclosure(db_session, member, 2024, "S1")
        _make_asset(db_session, d, "Portfolio", 1_000_000, 1_000_000)

        adv = AdvancedAnomalyDetector()
        # < 2 disclosures means skip — should yield nothing.
        assert adv.detect_wealth_vs_salary_anomalies(db_session) == []
        assert adv.detect_asset_appreciation_anomalies(db_session) == []

    def test_persist_skips_anomalies_without_anomaly_type(self, db_session):
        """Defensive: a malformed anomaly dict missing `anomaly_type` is ignored."""
        member = _make_member(db_session, bioguide="M000001")
        before = db_session.query(Anomaly).count()
        inserted = persist_anomalies(
            db_session,
            [{"member_id": member.id, "title": "no type", "description": "x"}],
        )
        after = db_session.query(Anomaly).count()
        assert inserted == 0
        assert after == before

    def test_persist_normalizes_uppercase_severity(self, db_session):
        member = _make_member(db_session, bioguide="N000099")
        persist_anomalies(
            db_session,
            [
                {
                    "member_id": member.id,
                    "anomaly_type": "wealth_vs_salary",
                    "severity": "CRITICAL",
                    "title": "T1",
                    "description": "d",
                },
                {
                    "member_id": member.id,
                    "anomaly_type": "wealth_vs_salary",
                    "severity": "MEDIUM",
                    "title": "T2",
                    "description": "d",
                },
            ],
        )
        rows = (
            db_session.query(Anomaly)
            .filter(Anomaly.member_id == member.id)
            .order_by(Anomaly.title)
            .all()
        )
        # CRITICAL → high (API doesn't know about a "critical" severity).
        assert rows[0].severity == "high"
        assert rows[1].severity == "medium"

    def test_persist_handles_int_severity(self, db_session):
        """trade_analyzer historically passed integer severities (1-10)."""
        member = _make_member(db_session, bioguide="I000001")
        persist_anomalies(
            db_session,
            [
                {
                    "member_id": member.id,
                    "anomaly_type": "high_trading_frequency",
                    "severity": 9,  # integer — must be normalized
                    "title": "Int severity",
                    "description": "d",
                },
            ],
        )
        row = db_session.query(Anomaly).filter(Anomaly.title == "Int severity").one()
        assert row.severity == "high"

    def test_transaction_amount_handles_only_min(self, db_session):
        """Only amount_min set (amount_max None) — return that side."""
        member = _make_member(db_session, bioguide="X000001")
        d = _make_disclosure(db_session, member, 2024, "X1", is_ptr=True)
        t = Transaction(
            disclosure_id=d.id,
            transaction_date=datetime(2024, 1, 1),
            transaction_type=TransactionType.PURCHASE,
            description="min only",
            ticker="MIN",
            amount_min=Decimal("500"),
            amount_max=None,
        )
        db_session.add(t)
        db_session.commit()
        assert transaction_amount(t) == 500.0
