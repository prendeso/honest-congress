"""The landing page is the first thing a visitor sees, and it made two claims.

`/api/insights` used to headline **"Most Flagged Member"** — one real person,
named above the fold, as the member with the most anomalies. A flag count is
not a fact about a person. It is dominated by trading volume: a member who
files many trades trips `large_trade`, `volume_spikes`, `trade_clustering` and
`high_trading_frequency` again and again. Presenting that as a ranking of
members, with no q-value and no caveat, is the thing the rest of this codebase
exists to refuse.

It also headlined **"Largest Single Trade: $5,000,000"**, read off `amount_max`
— the upper bound of a disclosed band — and printed as a figure. The filing
said $1,000,001–$5,000,000.

These tests hold both shut.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db import SessionLocal
from src.db.models import (
    Anomaly,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded():
    db = SessionLocal()
    for table in (Anomaly, Transaction, Disclosure, Member):
        db.query(table).delete()
    db.commit()

    member = Member(
        bioguide_id="LI00001",
        first_name="Land",
        last_name="Ing",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="NY",
        in_office=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 2, 1),
        document_id="LI-DOC-1",
        is_ptr=True,
        parsed=True,
        parse_confidence=1.0,
        has_text_layer=True,
    )
    scan = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 3, 1),
        document_id="LI-SCAN-1",
        is_ptr=True,
        parsed=True,
        parse_confidence=0.0,
        has_text_layer=False,
    )
    db.add_all([disclosure, scan])
    db.commit()
    db.refresh(disclosure)

    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 1, 15),
            transaction_type=TransactionType.PURCHASE,
            description="Example Corp",
            ticker="AAPL",
            amount_min=Decimal("1000001"),
            amount_max=Decimal("5000000"),
        )
    )
    db.add(
        Anomaly(
            member_id=member.id,
            anomaly_type="large_trade",
            severity="high",
            title="Large trade",
            description="A large trade",
        )
    )
    db.commit()
    yield db
    db.close()


def _cards(client):
    response = client.get("/api/insights")
    assert response.status_code == 200
    return response.json()


class TestNoCardNamesSomeoneForBeingFlagged:
    def test_no_member_is_headlined_by_flag_count(self, client, seeded):
        text = " ".join(f"{c['title']} {c['description']} {c['value']}" for c in _cards(client))

        assert "Most Flagged" not in text
        assert "Land Ing" not in text, (
            "a member is named on the landing page for their flag count, which is a "
            "count of thresholds crossed and not a fact about them"
        )

    def test_the_flag_card_says_what_a_flag_is_not(self, client, seeded):
        findings = next(c for c in _cards(client) if "survive" in c["title"].lower())
        assert "not a finding of wrongdoing" in findings["description"]


class TestAmountsAreBands:
    def test_the_largest_trade_is_shown_as_the_band_it_was_filed_as(self, client, seeded):
        card = next(c for c in _cards(client) if "argest" in c["title"])

        assert card["value"] == "$1,000,001–$5,000,000", (
            "the filing discloses a range; printing its upper bound as a figure "
            "states a number nobody disclosed"
        )
        assert "never an amount" in card["description"]


class TestTheReadableShareIsStated:
    def test_the_filings_card_says_how_many_could_not_be_read(self, client, seeded):
        card = next(c for c in _cards(client) if "iling" in c["title"])

        assert card["value"] == "2 filings"
        assert card["description"].startswith("1 of them could not be read"), card["description"]


class TestTheLateRateIsReported:
    def test_the_deadline_card_is_the_share_of_checked_trades(self, client, seeded):
        """The seeded trade was filed 17 days out, so nothing is late."""
        card = next(c for c in _cards(client) if "deadline" in c["title"].lower())

        assert card["value"] == "0.0%"
        assert "45 days" in card["description"]
