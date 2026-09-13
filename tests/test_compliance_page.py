"""The compliance page reads keys the compliance endpoints actually return.

These two endpoints return plain dicts rather than Pydantic models, so they
have no OpenAPI schema and `test_templates_read_served_fields.py` cannot check
them. This does the same job directly: it renders the page, pulls out every key
the template reads off a leaderboard row, and asserts each one is present in a
real response.

Worth having because the failure is silent. A renamed key becomes `undefined`
in the table cell, and the page shows a blank or a confident zero rather than
an error -- which is exactly how the Parsed Documents page shipped three
always-zero columns.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db import SessionLocal
from src.db.models import (
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

PAGE = Path("src/templates/compliance.html")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded():
    db = SessionLocal()
    for table in (Transaction, Disclosure, Member):
        db.query(table).delete()
    db.commit()

    member = Member(
        bioguide_id="CP00001",
        first_name="Comp",
        last_name="Liance",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="TX",
        in_office=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    # Filed 100 days after the trade: 55 days past the statutory 45.
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 4, 10),
        document_id="CP-DOC-1",
        is_ptr=True,
        parsed=True,
        parse_confidence=1.0,
        has_text_layer=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)

    for i in range(6):
        db.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=datetime(2024, 1, 1),
                transaction_type=TransactionType.PURCHASE,
                description=f"Example Corp {i}",
                ticker="AAPL",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
            )
        )
    db.commit()
    yield db
    db.close()


def _keys_read(prefix: str) -> set[str]:
    body = re.sub(r"<!--.*?-->", "", PAGE.read_text(), flags=re.S)
    return set(re.findall(rf"\b{prefix}\.([a-z_][a-z0-9_]*)\b", body))


class TestThePageAndTheEndpointsAgree:
    def test_every_row_key_the_page_reads_exists(self, client, seeded):
        """`m` is the row variable in both tables, so check against both shapes."""
        late = client.get("/api/compliance/?min_transactions=5").json()["members"]
        legible = client.get("/api/compliance/opacity/").json()["members"]
        assert late and legible, "the fixture should produce a row in each table"

        available = set(late[0]) | set(legible[0])
        missing = _keys_read("m") - {"components"} - available

        assert not missing, f"the page reads m.{sorted(missing)} and neither endpoint returns it"

    def test_the_worst_filing_tooltip_reads_real_keys(self, client, seeded):
        row = client.get("/api/compliance/?min_transactions=5").json()["members"][0]
        worst = row["worst_filing"]

        assert worst is not None, "the fixture files 55 days past the deadline"
        for key in ("ticker", "transaction_date", "filing_date"):
            assert key in worst

    def test_the_legibility_table_reads_keys_the_endpoint_returns(self, client, seeded):
        body = client.get("/api/compliance/opacity/").json()
        assert body["members"], "fixture should produce a scored member"

        components = body["members"][0]["components"]
        for key in _keys_read("m.components"):
            assert key in components, f"the page reads m.components.{key} and it is not returned"

    def test_both_tables_carry_their_disclaimer(self, client, seeded):
        """The note is what keeps a leaderboard from reading as an accusation."""
        assert "not their conduct" in client.get("/api/compliance/opacity/").json()["note"]
        assert "STOCK Act" in client.get("/api/compliance/").json()["note"]


class TestThePageIsReachable:
    def test_the_page_renders(self, client):
        response = client.get("/compliance")
        assert response.status_code == 200
        assert "Filing Compliance" in response.text

    def test_the_nav_links_to_it(self, client):
        assert 'href="/compliance"' in client.get("/").text
