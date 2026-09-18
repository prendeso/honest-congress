"""Tests for wealth analyzer."""

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.wealth_analyzer import WealthAnalyzer
from src.db.models import Asset, AssetType, Chamber, Disclosure, Member, Party
from tests.conftest import enabling_anomaly_type


class TestWealthAnalyzer:
    """Tests for WealthAnalyzer."""

    @pytest.fixture(autouse=True)
    def _wealth_growth_enabled(self, monkeypatch):
        """`excessive_wealth_growth` is held and the analyzer is skipped whole.

        These test what it does, so they lift the hold. The three below that
        assert nothing fires need it most: while held they would pass on an
        analyzer that never ran.
        """
        with enabling_anomaly_type("excessive_wealth_growth", monkeypatch):
            yield

    @pytest.fixture
    def analyzer(self):
        return WealthAnalyzer(threshold_percent=200, congressional_salary=174000)

    def test_analyze_member_no_disclosures(self, analyzer, db_session):
        """Test analyzing member with no disclosures."""
        member = Member(
            bioguide_id="W000001",
            first_name="New",
            last_name="Member",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
        )
        db_session.add(member)
        db_session.commit()

        anomalies = analyzer.analyze_member(db_session, member.id)
        assert len(anomalies) == 0

    def test_analyze_member_single_disclosure(self, analyzer, db_session):
        """Test analyzing member with only one disclosure."""
        member = Member(
            bioguide_id="W000002",
            first_name="One",
            last_name="Year",
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="TX",
        )
        db_session.add(member)
        db_session.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="Annual",
            filing_date=datetime(2024, 5, 15),
            document_id="SINGLE001",
            parsed=True,
        )
        db_session.add(disclosure)
        db_session.commit()

        # Need at least 2 years to compare
        anomalies = analyzer.analyze_member(db_session, member.id)
        assert len(anomalies) == 0

    def test_analyze_member_normal_growth(self, analyzer, db_session):
        """Test analyzing member with normal wealth growth."""
        member = Member(
            bioguide_id="W000003",
            first_name="Normal",
            last_name="Growth",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="NY",
        )
        db_session.add(member)
        db_session.commit()

        # Year 1: $500,000 net worth
        disclosure1 = Disclosure(
            member_id=member.id,
            filing_year=2023,
            filing_type="Annual",
            filing_date=datetime(2023, 5, 15),
            document_id="NORMAL001",
            parsed=True,
        )
        db_session.add(disclosure1)
        db_session.commit()

        asset1 = Asset(
            disclosure_id=disclosure1.id,
            asset_type=AssetType.STOCK,
            description="Test Portfolio",
            value_min=Decimal("500000"),
            value_max=Decimal("500000"),
        )
        db_session.add(asset1)

        # Year 2: $600,000 net worth (+$100,000, reasonable with salary)
        disclosure2 = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="Annual",
            filing_date=datetime(2024, 5, 15),
            document_id="NORMAL002",
            parsed=True,
        )
        db_session.add(disclosure2)
        db_session.commit()

        asset2 = Asset(
            disclosure_id=disclosure2.id,
            asset_type=AssetType.STOCK,
            description="Test Portfolio",
            value_min=Decimal("600000"),
            value_max=Decimal("600000"),
        )
        db_session.add(asset2)
        db_session.commit()

        anomalies = analyzer.analyze_member(db_session, member.id)
        # 20% growth with $174k salary on $500k base = ~35% allowed, so no anomaly
        assert len(anomalies) == 0

    def test_analyze_member_suspicious_growth(self, analyzer, db_session):
        """Test analyzing member with suspicious wealth growth."""
        member = Member(
            bioguide_id="W000004",
            first_name="Suspicious",
            last_name="Growth",
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="FL",
        )
        db_session.add(member)
        db_session.commit()

        # Year 1: $500,000 net worth
        disclosure1 = Disclosure(
            member_id=member.id,
            filing_year=2023,
            filing_type="Annual",
            filing_date=datetime(2023, 5, 15),
            document_id="SUS001",
            parsed=True,
        )
        db_session.add(disclosure1)
        db_session.commit()

        asset1 = Asset(
            disclosure_id=disclosure1.id,
            asset_type=AssetType.STOCK,
            description="Test Portfolio",
            value_min=Decimal("500000"),
            value_max=Decimal("500000"),
        )
        db_session.add(asset1)

        # Year 2: $5,000,000 net worth (+$4.5M, way more than salary!)
        disclosure2 = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="Annual",
            filing_date=datetime(2024, 5, 15),
            document_id="SUS002",
            parsed=True,
        )
        db_session.add(disclosure2)
        db_session.commit()

        asset2 = Asset(
            disclosure_id=disclosure2.id,
            asset_type=AssetType.STOCK,
            description="Test Portfolio",
            value_min=Decimal("5000000"),
            value_max=Decimal("5000000"),
        )
        db_session.add(asset2)
        db_session.commit()

        anomalies = analyzer.analyze_member(db_session, member.id)

        # 900% growth is way above threshold
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "excessive_wealth_growth"
        assert anomalies[0]["severity"] == "high"

    def test_calculate_severity(self, analyzer):
        """Test severity calculation."""
        assert analyzer._calculate_severity(600, 35) == "high"  # 565% deviation
        assert analyzer._calculate_severity(300, 35) == "medium"  # 265% deviation
        assert analyzer._calculate_severity(100, 35) == "low"  # 65% deviation
