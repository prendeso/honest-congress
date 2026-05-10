"""Tests for Tier-2 detectors: donor conflict, lobbying overlap, contract front-run.

Each detector scans existing transactions for trades that fall inside a
suspicious time window relative to a trigger event from QuiverQuant. These
tests seed both halves (transactions + trigger events) and assert on
which trades get flagged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

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


class TestClientEndpointRouting:
    """Tier-2 and Tier-3 client methods build the right URLs and pass the
    right query params. Same pattern as the existing test_quiverquant.py."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr("src.ingestion.quiverquant.time.sleep", lambda *_: None)

    @pytest.fixture
    def client(self, monkeypatch):
        from src.ingestion.quiverquant import QuiverQuantClient

        monkeypatch.setenv("QUIVERQUANT_API_KEY", "test-token")
        return QuiverQuantClient()

    def _mock_response(self, payload, status_code=200):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.status_code = status_code
        r.headers = {}
        r.json.return_value = payload
        return r

    def test_corporate_donors_passes_filters(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_corporate_donors(bioguide_id="A000001", cycle="2024")

        url = mock_get.call_args.args[0]
        params = mock_get.call_args.kwargs.get("params") or {}
        assert "/bulk/corporatedonors" in url
        assert params == {"bioguide_id": "A000001", "cycle": "2024"}

    def test_lobbying_with_ticker_uses_historical(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_lobbying(ticker="AAPL")
        assert "/historical/lobbying/AAPL" in mock_get.call_args.args[0]

    def test_lobbying_without_ticker_uses_live(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_lobbying()
        assert "/live/lobbying" in mock_get.call_args.args[0]

    def test_government_contracts_routing(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_government_contracts(ticker="LMT")
        assert "/historical/govcontractsall/LMT" in mock_get.call_args.args[0]

    def test_insiders_passes_ticker_as_query_param(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_insiders(ticker="NVDA")
        assert "/live/insiders" in mock_get.call_args.args[0]
        assert mock_get.call_args.kwargs.get("params") == {"ticker": "NVDA"}

    def test_top_shareholders_uses_path_param(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_top_shareholders("MSFT")
        assert "/live/topshareholders/MSFT" in mock_get.call_args.args[0]

    def test_news_passes_pagination_params(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_news(ticker="AAPL", page=2, page_size=50)
        params = mock_get.call_args.kwargs.get("params") or {}
        assert params == {"ticker": "AAPL", "page": "2", "page_size": "50"}

    def test_sec13f_owner_filter(self, client):
        from unittest.mock import patch

        with patch.object(client.session, "get", return_value=self._mock_response([])) as mock_get:
            client.get_sec13f(owner="Berkshire Hathaway")
        assert "/live/sec13f" in mock_get.call_args.args[0]
        assert mock_get.call_args.kwargs.get("params") == {"owner": "Berkshire Hathaway"}
