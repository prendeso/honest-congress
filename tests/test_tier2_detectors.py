"""Tests for Tier-2 detectors: donor conflict, lobbying overlap, contract front-run.

Each detector scans existing transactions for trades that fall inside a
suspicious time window relative to a trigger event from QuiverQuant. These
tests seed both halves (transactions + trigger events) and assert on
which trades get flagged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.tier2_detectors import (
    detect_contract_front_runs,
    detect_donor_conflicts,
    detect_lobbying_overlaps,
    run_tier2_detection,
)
from src.db.models import (
    Anomaly,
    CampaignDonation,
    Chamber,
    Disclosure,
    GovernmentContract,
    LobbyingDisclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


def _make_member(db, bioguide="M000001", first="Test", last="Member") -> Member:
    m = Member(
        bioguide_id=bioguide,
        first_name=first,
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
        in_office=True,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_trade(
    db,
    member: Member,
    ticker: str,
    txn_type: TransactionType,
    when: datetime,
) -> Transaction:
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=when.year,
        filing_type="PTR",
        filing_date=when,
        document_id=f"DOC_{member.id}_{ticker}_{when.isoformat()}",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)

    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=txn_type,
        description=f"{ticker} test",
        ticker=ticker,
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# ---------------- donor conflict ----------------


class TestDonorConflict:
    def test_flags_trade_after_donation_within_window(self, db_session):
        member = _make_member(db_session)
        donation_date = datetime(2024, 6, 1)
        db_session.add(
            CampaignDonation(
                member_id=member.id,
                ticker="AAPL",
                donor_name="Apple Inc",
                amount=Decimal("5000"),
                donation_date=donation_date,
            )
        )
        db_session.commit()

        # Trade 30 days after donation — inside default 90-day window
        _make_trade(
            db_session,
            member,
            "AAPL",
            TransactionType.PURCHASE,
            donation_date + timedelta(days=30),
        )

        anomalies = detect_donor_conflicts(db_session)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "donor_conflict"
        assert anomalies[0]["member_id"] == member.id
        # 30 days in -> high severity per the detector's thresholds
        assert anomalies[0]["severity"] == "HIGH"

    def test_flags_trade_before_donation(self, db_session):
        """Detector is symmetric — trade before donation also flagged."""
        member = _make_member(db_session, bioguide="M000002")
        donation_date = datetime(2024, 6, 1)
        db_session.add(
            CampaignDonation(
                member_id=member.id,
                ticker="MSFT",
                donor_name="Microsoft Corp",
                donation_date=donation_date,
            )
        )
        db_session.commit()

        _make_trade(
            db_session,
            member,
            "MSFT",
            TransactionType.SALE,
            donation_date - timedelta(days=20),
        )

        anomalies = detect_donor_conflicts(db_session)
        assert len(anomalies) == 1
        assert "before" in anomalies[0]["description"]

    def test_skips_trade_outside_window(self, db_session):
        member = _make_member(db_session, bioguide="M000003")
        donation_date = datetime(2024, 6, 1)
        db_session.add(
            CampaignDonation(
                member_id=member.id,
                ticker="GOOG",
                donor_name="Alphabet",
                donation_date=donation_date,
            )
        )
        db_session.commit()

        # 200 days out — outside default 90-day window
        _make_trade(
            db_session,
            member,
            "GOOG",
            TransactionType.PURCHASE,
            donation_date + timedelta(days=200),
        )

        assert detect_donor_conflicts(db_session) == []

    def test_does_not_match_other_members_trades(self, db_session):
        """Donation to A shouldn't flag B's trades."""
        member_a = _make_member(db_session, bioguide="A000001", last="Alice")
        member_b = _make_member(db_session, bioguide="B000001", last="Bob")
        donation_date = datetime(2024, 6, 1)
        db_session.add(
            CampaignDonation(
                member_id=member_a.id,
                ticker="NVDA",
                donor_name="NVIDIA",
                donation_date=donation_date,
            )
        )
        db_session.commit()

        _make_trade(
            db_session,
            member_b,
            "NVDA",
            TransactionType.PURCHASE,
            donation_date + timedelta(days=30),
        )

        assert detect_donor_conflicts(db_session) == []


# ---------------- lobbying overlap ----------------


class TestLobbyingOverlap:
    def test_flags_trade_near_filing(self, db_session):
        member = _make_member(db_session)
        filed_date = datetime(2024, 5, 15)
        db_session.add(
            LobbyingDisclosure(
                ticker="LMT",
                registrant="Lockheed Martin",
                filed_date=filed_date,
                amount=Decimal("250000"),
            )
        )
        db_session.commit()

        # Trade 10 days after filing — inside default 30-day window
        _make_trade(
            db_session,
            member,
            "LMT",
            TransactionType.PURCHASE,
            filed_date + timedelta(days=10),
        )

        anomalies = detect_lobbying_overlaps(db_session)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "lobbying_overlap"
        assert "Lockheed Martin" in anomalies[0]["description"]

    def test_flags_any_member_for_a_filing(self, db_session):
        """Lobbying signal isn't member-specific — anyone trading near
        the filing is in scope."""
        a = _make_member(db_session, bioguide="A000010", last="A")
        b = _make_member(db_session, bioguide="B000010", last="B")
        filed_date = datetime(2024, 7, 1)
        db_session.add(
            LobbyingDisclosure(
                ticker="PFE",
                registrant="Pfizer",
                filed_date=filed_date,
            )
        )
        db_session.commit()

        _make_trade(db_session, a, "PFE", TransactionType.PURCHASE, filed_date - timedelta(days=5))
        _make_trade(db_session, b, "PFE", TransactionType.SALE, filed_date + timedelta(days=15))

        anomalies = detect_lobbying_overlaps(db_session)
        flagged_member_ids = {a["member_id"] for a in anomalies}
        assert flagged_member_ids == {a.id, b.id} or len(anomalies) == 2


# ---------------- contract front-run ----------------


class TestContractFrontRun:
    def test_flags_purchase_before_award(self, db_session):
        member = _make_member(db_session)
        awarded = datetime(2024, 8, 1)
        db_session.add(
            GovernmentContract(
                ticker="RTX",
                agency="Department of Defense",
                amount=Decimal("100000000"),
                awarded_date=awarded,
            )
        )
        db_session.commit()

        # Buy 14 days before award — inside default 30-day window
        _make_trade(
            db_session,
            member,
            "RTX",
            TransactionType.PURCHASE,
            awarded - timedelta(days=14),
        )

        anomalies = detect_contract_front_runs(db_session)
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "contract_front_run"
        assert anomalies[0]["severity"] == "HIGH"

    def test_does_not_flag_sale(self, db_session):
        """Detector is asymmetric — only PURCHASES front-running awards."""
        member = _make_member(db_session, bioguide="S000001")
        awarded = datetime(2024, 9, 1)
        db_session.add(GovernmentContract(ticker="BA", awarded_date=awarded))
        db_session.commit()

        _make_trade(
            db_session,
            member,
            "BA",
            TransactionType.SALE,
            awarded - timedelta(days=10),
        )
        assert detect_contract_front_runs(db_session) == []

    def test_does_not_flag_purchase_after_award(self, db_session):
        """Buying after the award is publicly known information — no flag."""
        member = _make_member(db_session, bioguide="P000001")
        awarded = datetime(2024, 9, 1)
        db_session.add(GovernmentContract(ticker="GD", awarded_date=awarded))
        db_session.commit()

        _make_trade(
            db_session,
            member,
            "GD",
            TransactionType.PURCHASE,
            awarded + timedelta(days=10),
        )
        assert detect_contract_front_runs(db_session) == []


class TestDeobligationsAreNotAwards:
    """USASpending's feed is award *actions*, and an action can remove money.

    Lockheed Martin's largest single action since 2023, by absolute size, is a
    -$1.88bn deobligation. Before this, a purchase in the thirty days before it
    was reported as a member buying ahead of the company being "awarded a
    federal contract ... ($-1,882,437,667)".
    """

    def _purchase_before(self, db, *, ticker: str, amount, bioguide: str):
        member = _make_member(db, bioguide=bioguide)
        awarded = datetime(2024, 8, 1)
        db.add(
            GovernmentContract(
                ticker=ticker,
                agency="Department of Defense",
                amount=amount,
                awarded_date=awarded,
            )
        )
        db.commit()
        _make_trade(db, member, ticker, TransactionType.PURCHASE, awarded - timedelta(days=14))
        return detect_contract_front_runs(db)

    def test_a_deobligation_is_not_an_award(self, db_session):
        assert (
            self._purchase_before(
                db_session, ticker="LMT", amount=Decimal("-1882437667.02"), bioguide="D000001"
            )
            == []
        )

    def test_a_zero_dollar_modification_is_not_an_award(self, db_session):
        """An administrative restructure obligates nothing, so it front-runs nothing."""
        assert (
            self._purchase_before(db_session, ticker="NOC", amount=Decimal("0"), bioguide="Z000001")
            == []
        )

    def test_an_award_with_no_amount_is_still_an_award(self, db_session):
        """A missing figure is not evidence of a deobligation.

        Dropping these would narrow coverage on the strength of an absent field,
        which is a different and worse error than the one above.
        """
        found = self._purchase_before(db_session, ticker="GD", amount=None, bioguide="N000001")
        assert len(found) == 1

    def test_the_null_model_sees_the_same_events_the_detector_does(self, db_session):
        """The q-value has to be measured against the events it was drawn from.

        If `_collect_contracts` counted deobligations the detector refuses, the
        permutation test would shuffle trades against award dates no finding
        could ever have come from, and every contract q-value would be deflated
        by events that are not awards. The two call the same function; this
        asserts they still agree.
        """
        from src.analysis.significance import _collect_contracts

        member = _make_member(db_session, bioguide="Q000001")
        awarded = datetime(2024, 8, 1)
        db_session.add_all(
            [
                GovernmentContract(ticker="RTX", amount=Decimal("5000000"), awarded_date=awarded),
                GovernmentContract(
                    ticker="RTX",
                    amount=Decimal("-9000000"),
                    awarded_date=awarded + timedelta(days=200),
                ),
            ]
        )
        db_session.commit()
        _make_trade(
            db_session, member, "RTX", TransactionType.PURCHASE, awarded - timedelta(days=14)
        )

        streams = _collect_contracts(db_session)
        _, event_dates = streams[member.id]["RTX"]
        assert len(event_dates) == 1


# ---------------- orchestration ----------------


class TestRunTier2Detection:
    def test_persists_to_anomaly_table(self, db_session):
        member = _make_member(db_session)
        donation_date = datetime(2024, 6, 1)
        db_session.add(
            CampaignDonation(
                member_id=member.id,
                ticker="AAPL",
                donor_name="Apple Inc",
                donation_date=donation_date,
            )
        )
        db_session.commit()
        _make_trade(
            db_session,
            member,
            "AAPL",
            TransactionType.PURCHASE,
            donation_date + timedelta(days=10),
        )

        before = db_session.query(Anomaly).count()
        result = run_tier2_detection(db_session)
        after = db_session.query(Anomaly).count()

        assert result["total"] >= 1
        assert after > before
        # Verify anomaly_type lands in the DB
        types = {a.anomaly_type for a in db_session.query(Anomaly).all()}
        assert "donor_conflict" in types

    def test_empty_tables_run_clean(self, db_session):
        """No tier-2 data → no anomalies, no errors."""
        result = run_tier2_detection(db_session)
        assert result["total"] == 0
        assert result["persisted"] == 0


# ---------------- client methods (Tier 2 + Tier 3 endpoint routing) ----------------


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


class TestCostDoesNotTrackTheTriggerTable:
    """Each detector used to ask the database once per trigger row.

    That is an N+1 whose N is set by an external feed rather than by anything
    here: lobbying holds 4,920 rows, and the contract feed went from roughly 200
    to roughly 10,000 the moment it stopped taking a global top-300 slice. Every
    one of those is a network round trip, because `analyze` runs on a GitHub
    runner against a hosted database.

    Measured on 8,000 trades, 4,920 lobbying filings, 10,000 contract awards and
    2,000 donations, with identical findings from both versions:

        detect_donor_conflicts        2,001 queries -> 2
        detect_lobbying_overlaps      4,921 queries -> 2
        detect_contract_front_runs   10,001 queries -> 2

    The assertion below is the shape of that, not the size: ten trigger rows
    must not cost ten times what one costs. A count is asserted rather than a
    duration because a timing test is flaky and does not say what broke.
    """

    def _seed(self, db_session, rows: int, tag: str = "a"):
        member = _make_member(db_session, bioguide=f"C00000{tag}")
        _make_trade(db_session, member, "AAPL", TransactionType.PURCHASE, datetime(2024, 3, 10))
        for i in range(rows):
            db_session.add(
                LobbyingDisclosure(
                    ticker="AAPL",
                    registrant=f"Registrant {i}",
                    client="Apple Inc.",
                    filed_date=datetime(2024, 3, 1) + timedelta(days=i),
                    source="senate-lda",
                    external_id=f"LOB{tag}{i}",
                )
            )
            db_session.add(
                GovernmentContract(
                    ticker="AAPL",
                    agency="Department of Defense",
                    description=f"Award {i}",
                    amount=Decimal("1000000"),
                    awarded_date=datetime(2024, 3, 12) + timedelta(days=i),
                    source="usaspending",
                    external_id=f"GOV{tag}{i}",
                )
            )
            db_session.add(
                CampaignDonation(
                    member_id=member.id,
                    ticker="AAPL",
                    donor_name=f"Apple PAC {i}",
                    amount=Decimal("5000"),
                    donation_date=datetime(2024, 3, 5) + timedelta(days=i),
                    cycle="2024",
                    source="fec",
                    external_id=f"FEC{tag}{i}",
                )
            )
        db_session.commit()

    def test_one_trigger_row_and_ten_cost_the_same(self, db_session, engine):
        self._seed(db_session, 1, tag="a")
        one = {
            name: len(_count_queries(engine, lambda fn=fn: fn(db_session)))
            for name, fn in (
                ("donor", detect_donor_conflicts),
                ("lobbying", detect_lobbying_overlaps),
                ("contract", detect_contract_front_runs),
            )
        }

        self._seed(db_session, 10, tag="b")
        ten = {
            name: len(_count_queries(engine, lambda fn=fn: fn(db_session)))
            for name, fn in (
                ("donor", detect_donor_conflicts),
                ("lobbying", detect_lobbying_overlaps),
                ("contract", detect_contract_front_runs),
            )
        }

        assert one == ten, (
            f"query count grew with the number of trigger rows: {one} -> {ten}. "
            "That is the N+1 this was written to stop."
        )

    def test_a_detector_reads_its_trigger_table_and_the_trades_once_each(self, db_session, engine):
        self._seed(db_session, 25)
        statements = _count_queries(engine, lambda: detect_lobbying_overlaps(db_session))
        assert len(statements) == 2, statements

    def test_a_detector_with_no_trigger_rows_does_not_ask_about_trades(self, db_session, engine):
        _make_member(db_session)
        statements = _count_queries(engine, lambda: detect_contract_front_runs(db_session))
        # Just the scan of the empty trigger table. There is no ticker to ask
        # about, and `IN ()` against an empty set is a query that cannot match.
        assert len(statements) == 1, statements
