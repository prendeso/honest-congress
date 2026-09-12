"""STOCK Act filing compliance.

The least interpretive output the project produces: subtraction between two
dates that both appear on the filing. It makes no claim about intent, timing,
profit or conflict, which is exactly why it is worth publishing.

Distinct from the `late_filing` detector, which flags individual materially-late
trades above a dollar threshold to keep the anomaly table readable. A compliance
*rate* has to count every covered transaction or it is not a rate.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.analysis.compliance import PTR_DEADLINE_DAYS, compliance_leaderboard, member_compliance
from src.api.main import app
from src.db import SessionLocal
from src.db.models import (
    Asset,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

TXN_DATE = datetime(2024, 3, 1)


@pytest.fixture
def db():
    session = SessionLocal()
    for table in (Transaction, Asset, Disclosure, Member):
        session.query(table).delete()
    session.commit()
    yield session
    session.close()


def _member(db, bioguide="CP00001", last="Filer"):
    m = Member(
        bioguide_id=bioguide,
        first_name="Comp",
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="MT",
        in_office=True,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _filing(db, member, days_after_transaction, doc_id, amount_max="15000", is_ptr=True):
    """One PTR containing one transaction, filed N days after the trade."""
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=TXN_DATE + timedelta(days=days_after_transaction),
        document_id=doc_id,
        is_ptr=is_ptr,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    db.add(
        Transaction(
            disclosure_id=d.id,
            transaction_date=TXN_DATE,
            transaction_type=TransactionType.PURCHASE,
            description="AAPL",
            ticker="AAPL",
            amount_min=Decimal("1001"),
            amount_max=Decimal(amount_max),
        )
    )
    db.commit()
    return d


class TestMemberCompliance:
    def test_on_time_filing_is_not_late(self, db):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS - 1, "OK-1")

        score = member_compliance(db, m)

        assert score["transactions_checked"] == 1
        assert score["filed_late"] == 0
        assert score["late_rate_percent"] == 0.0

    def test_exactly_on_the_deadline_is_not_late(self, db):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS, "EDGE-1")

        assert member_compliance(db, m)["filed_late"] == 0

    def test_one_day_over_is_late(self, db):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS + 1, "LATE-1")

        score = member_compliance(db, m)

        assert score["filed_late"] == 1
        assert score["max_days_late"] == 1

    def test_rate_counts_small_trades_too(self, db):
        # The late_filing detector ignores trades under a dollar threshold; a
        # compliance rate must not, or the denominator is wrong.
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS + 10, "SMALL-1", amount_max="1001")

        score = member_compliance(db, m)

        assert score["transactions_checked"] == 1
        assert score["filed_late"] == 1
        assert score["late_rate_percent"] == 100.0

    def test_mixed_record_produces_a_rate(self, db):
        m = _member(db)
        _filing(db, m, 10, "A")
        _filing(db, m, 20, "B")
        _filing(db, m, PTR_DEADLINE_DAYS + 30, "C")
        _filing(db, m, PTR_DEADLINE_DAYS + 60, "D")

        score = member_compliance(db, m)

        assert score["transactions_checked"] == 4
        assert score["filed_late"] == 2
        assert score["on_time"] == 2
        assert score["late_rate_percent"] == 50.0
        assert score["mean_days_late"] == 45.0
        assert score["max_days_late"] == 60

    def test_worst_filing_is_reported(self, db):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS + 5, "MILD")
        _filing(db, m, PTR_DEADLINE_DAYS + 200, "TERRIBLE")

        worst = member_compliance(db, m)["worst_filing"]

        assert worst["days_late"] == 200
        assert worst["document_id"] == "TERRIBLE"

    def test_member_with_nothing_checkable_returns_none(self, db):
        m = _member(db)

        assert member_compliance(db, m) is None

    def test_annual_filings_are_not_counted(self, db):
        # PTR deadlines apply to transaction reports, not annual disclosures.
        m = _member(db)
        _filing(db, m, 300, "ANNUAL", is_ptr=False)

        assert member_compliance(db, m) is None


class TestLeaderboard:
    def test_ranks_worst_offender_first(self, db):
        clean = _member(db, "CP00002", "Clean")
        messy = _member(db, "CP00003", "Messy")
        for i in range(5):
            _filing(db, clean, 10, f"CLEAN-{i}")
        for i in range(5):
            _filing(db, messy, PTR_DEADLINE_DAYS + 50, f"MESSY-{i}")

        board = compliance_leaderboard(db, min_transactions=5)

        assert board["members"][0]["member_name"].endswith("Messy")
        assert board["members"][0]["late_rate_percent"] == 100.0

    def test_min_transactions_excludes_tiny_samples(self, db):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS + 100, "ONLY-1")

        # One late filing out of one transaction should not top a "100% late"
        # ranking.
        assert compliance_leaderboard(db, min_transactions=5)["members"] == []
        assert len(compliance_leaderboard(db, min_transactions=1)["members"]) == 1

    def test_overall_rate_aggregates_across_members(self, db):
        a = _member(db, "CP00004", "One")
        b = _member(db, "CP00005", "Two")
        for i in range(5):
            _filing(db, a, 10, f"A-{i}")
        for i in range(5):
            _filing(db, b, PTR_DEADLINE_DAYS + 5, f"B-{i}")

        board = compliance_leaderboard(db, min_transactions=5)

        assert board["total_transactions_checked"] == 10
        assert board["total_filed_late"] == 5
        assert board["overall_late_rate_percent"] == 50.0

    def test_note_states_the_methodology(self, db):
        board = compliance_leaderboard(db)

        assert str(PTR_DEADLINE_DAYS) in board["note"]
        assert "Only Periodic Transaction Reports are counted" in board["note"]


class TestComplianceApi:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_leaderboard_endpoint(self, db, client):
        m = _member(db)
        for i in range(5):
            _filing(db, m, PTR_DEADLINE_DAYS + 10, f"API-{i}")

        r = client.get("/api/compliance/?min_transactions=5")

        assert r.status_code == 200
        body = r.json()
        assert body["members_ranked"] == 1
        assert body["members"][0]["late_rate_percent"] == 100.0

    def test_member_endpoint(self, db, client):
        m = _member(db)
        _filing(db, m, PTR_DEADLINE_DAYS + 3, "ONE")

        r = client.get(f"/api/compliance/{m.id}")

        assert r.status_code == 200
        assert r.json()["max_days_late"] == 3

    def test_unknown_member_is_404(self, db, client):
        assert client.get("/api/compliance/999999").status_code == 404

    def test_member_with_no_checkable_filings_is_404(self, db, client):
        m = _member(db)

        assert client.get(f"/api/compliance/{m.id}").status_code == 404
