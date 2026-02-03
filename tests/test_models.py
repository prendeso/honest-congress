"""Tests for database models."""
import pytest
from datetime import datetime
from decimal import Decimal

from src.db.models import (
    Member, Disclosure, Asset, Transaction, Liability, Anomaly,
    Chamber, Party, TransactionType, AssetType
)


class TestMember:
    """Tests for Member model."""

    def test_create_member(self, db_session):
        """Test creating a member."""
        member = Member(
            bioguide_id="T000001",
            first_name="Test",
            last_name="Member",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
            district="12",
        )
        db_session.add(member)
        db_session.commit()

        assert member.id is not None
        assert member.bioguide_id == "T000001"
        assert member.chamber == Chamber.HOUSE
        assert member.party == Party.DEMOCRAT

    def test_member_repr(self, db_session):
        """Test member string representation."""
        member = Member(
            bioguide_id="T000002",
            first_name="Jane",
            last_name="Doe",
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="TX",
        )
        db_session.add(member)
        db_session.commit()

        assert "Jane Doe" in repr(member)
        assert "R-TX" in repr(member)


class TestDisclosure:
    """Tests for Disclosure model."""

    def test_create_disclosure(self, db_session):
        """Test creating a disclosure."""
        member = Member(
            bioguide_id="T000003",
            first_name="Test",
            last_name="Person",
            chamber=Chamber.HOUSE,
            party=Party.INDEPENDENT,
            state="VT",
        )
        db_session.add(member)
        db_session.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="Annual",
            filing_date=datetime(2024, 5, 15),
            document_id="DOC123456",
            document_url="https://example.com/doc.pdf",
        )
        db_session.add(disclosure)
        db_session.commit()

        assert disclosure.id is not None
        assert disclosure.member_id == member.id
        assert disclosure.filing_year == 2024


class TestAsset:
    """Tests for Asset model."""

    def test_create_asset(self, db_session):
        """Test creating an asset."""
        member = Member(
            bioguide_id="T000004",
            first_name="Asset",
            last_name="Tester",
            chamber=Chamber.SENATE,
            party=Party.DEMOCRAT,
            state="NY",
        )
        db_session.add(member)
        db_session.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="Annual",
            filing_date=datetime.now(),
            document_id="DOC789",
        )
        db_session.add(disclosure)
        db_session.commit()

        asset = Asset(
            disclosure_id=disclosure.id,
            asset_type=AssetType.STOCK,
            description="Apple Inc (AAPL) - Common Stock",
            ticker="AAPL",
            value_min=Decimal("100001"),
            value_max=Decimal("250000"),
        )
        db_session.add(asset)
        db_session.commit()

        assert asset.id is not None
        assert asset.ticker == "AAPL"
        assert asset.value_min == Decimal("100001")


class TestTransaction:
    """Tests for Transaction model."""

    def test_create_transaction(self, db_session):
        """Test creating a transaction."""
        member = Member(
            bioguide_id="T000005",
            first_name="Trade",
            last_name="Master",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="FL",
        )
        db_session.add(member)
        db_session.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=datetime.now(),
            document_id="PTR001",
        )
        db_session.add(disclosure)
        db_session.commit()

        transaction = Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 3, 15),
            transaction_type=TransactionType.PURCHASE,
            description="Microsoft Corporation (MSFT)",
            ticker="MSFT",
            amount_min=Decimal("15001"),
            amount_max=Decimal("50000"),
            owner="Self",
        )
        db_session.add(transaction)
        db_session.commit()

        assert transaction.id is not None
        assert transaction.transaction_type == TransactionType.PURCHASE
        assert transaction.ticker == "MSFT"


class TestAnomaly:
    """Tests for Anomaly model."""

    def test_create_anomaly(self, db_session):
        """Test creating an anomaly."""
        member = Member(
            bioguide_id="T000006",
            first_name="Sus",
            last_name="Picious",
            chamber=Chamber.SENATE,
            party=Party.DEMOCRAT,
            state="CA",
        )
        db_session.add(member)
        db_session.commit()

        anomaly = Anomaly(
            member_id=member.id,
            anomaly_type="excessive_wealth_growth",
            severity="high",
            title="Wealth growth of 500% exceeds salary-based expectation",
            description="Net worth grew significantly more than congressional salary would allow.",
            computed_value=Decimal("5000000"),
            threshold_value=Decimal("174000"),
        )
        db_session.add(anomaly)
        db_session.commit()

        assert anomaly.id is not None
        assert anomaly.severity == "high"
        assert anomaly.anomaly_type == "excessive_wealth_growth"

