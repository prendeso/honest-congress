"""Net worth must subtract liabilities.

`_calculate_net_worth` summed assets and then hardcoded liabilities to zero,
with a comment claiming they were "accounted for". The Liability table was
populated by ingestion and never read, so every wealth-growth figure was gross
assets presented as net worth -- inflating the detectors built on it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.wealth_analyzer import WealthAnalyzer
from src.db.models import Asset, AssetType, Chamber, Disclosure, Liability, Member, Party


@pytest.fixture
def disclosure(db_session):
    member = Member(
        bioguide_id="W000001",
        first_name="Net",
        last_name="Worth",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="NY",
    )
    db_session.add(member)
    db_session.commit()
    db_session.refresh(member)

    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="FD",
        filing_date=datetime(2024, 5, 15),
        document_id="NW-1",
        parsed=True,
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _add_asset(db, disclosure, low, high):
    db.add(
        Asset(
            disclosure_id=disclosure.id,
            asset_type=AssetType.STOCK,
            description="Some holding",
            value_min=Decimal(low),
            value_max=Decimal(high),
        )
    )
    db.commit()


def _add_liability(db, disclosure, low, high):
    db.add(
        Liability(
            disclosure_id=disclosure.id,
            creditor="Some Bank",
            description="Mortgage",
            amount_min=Decimal(low),
            amount_max=Decimal(high),
        )
    )
    db.commit()


class TestNetWorthSubtractsLiabilities:
    def test_assets_only_is_unchanged(self, db_session, disclosure):
        _add_asset(db_session, disclosure, 100_000, 200_000)

        result = WealthAnalyzer()._calculate_net_worth(db_session, disclosure.id)

        assert result["min"] == Decimal(100_000)
        assert result["max"] == Decimal(200_000)

    def test_liabilities_reduce_net_worth(self, db_session, disclosure):
        _add_asset(db_session, disclosure, 100_000, 200_000)
        _add_liability(db_session, disclosure, 50_000, 80_000)

        result = WealthAnalyzer()._calculate_net_worth(db_session, disclosure.id)

        # Widest honest interval: lowest assets minus highest debt, and inverse.
        assert result["min"] == Decimal(20_000)
        assert result["max"] == Decimal(150_000)

    def test_debt_can_exceed_assets(self, db_session, disclosure):
        _add_asset(db_session, disclosure, 10_000, 20_000)
        _add_liability(db_session, disclosure, 100_000, 200_000)

        result = WealthAnalyzer()._calculate_net_worth(db_session, disclosure.id)

        assert result["min"] < 0, "a member can be net negative; this must not clamp to zero"

    def test_multiple_liabilities_are_summed(self, db_session, disclosure):
        _add_asset(db_session, disclosure, 500_000, 500_000)
        _add_liability(db_session, disclosure, 100_000, 100_000)
        _add_liability(db_session, disclosure, 50_000, 50_000)

        result = WealthAnalyzer()._calculate_net_worth(db_session, disclosure.id)

        assert result["min"] == Decimal(350_000)

    def test_no_rows_at_all_is_zero_not_an_error(self, db_session, disclosure):
        result = WealthAnalyzer()._calculate_net_worth(db_session, disclosure.id)

        assert result["min"] == Decimal(0)
        assert result["max"] == Decimal(0)
