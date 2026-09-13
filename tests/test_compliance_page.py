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

# Anchored to this file, not the working directory. Built with a bare
# `Path("src/templates")` these globs came up empty when pytest ran from
# anywhere but the repo root, the parametrised cases vanished, and pytest
# reported them SKIPPED rather than failing -- the same silent-pass failure
# these tests exist to catch.
REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "src" / "templates" / "compliance.html"

assert PAGE.exists(), f"{PAGE} is missing; this file would test nothing"


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


class TestTheLeaderboardsDoNotQueryPerMember:
    """These walked the whole roster, one query per member.

    On the production roster of 12,766 members that is 12,766 round trips for
    the compliance board and roughly three times that for opacity. Measured
    against the live site it took 48 seconds to return an EMPTY leaderboard --
    and `/api/insights` called it on every load, so the landing page of a
    public site hung.

    Counting queries rather than timing anything: a timing assertion is flaky
    and does not say what went wrong, whereas "it issued one query per member"
    is the actual defect and stays true on any machine.
    """

    @staticmethod
    def _count_queries(callable_):
        from sqlalchemy import event

        from src.db import engine

        seen: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            callable_()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return seen

    def _many_members(self, db, count):
        from src.db.models import Chamber, Member, Party

        for i in range(count):
            db.add(
                Member(
                    bioguide_id=f"Q{i:06d}",
                    first_name="Quer",
                    last_name=f"Y{i}",
                    chamber=Chamber.HOUSE,
                    party=Party.DEMOCRAT,
                    state="NY",
                )
            )
        db.commit()

    def test_compliance_board_cost_does_not_grow_with_the_roster(self, client, seeded):
        from src.analysis.compliance import compliance_leaderboard

        before = len(self._count_queries(lambda: compliance_leaderboard(seeded)))
        self._many_members(seeded, 60)
        after = len(self._count_queries(lambda: compliance_leaderboard(seeded)))

        assert after <= before + 1, (
            f"adding 60 members added {after - before} queries -- this is per-member again"
        )

    def test_opacity_board_cost_does_not_grow_with_the_roster(self, client, seeded):
        from src.analysis.opacity import opacity_leaderboard

        before = len(self._count_queries(lambda: opacity_leaderboard(seeded)))
        self._many_members(seeded, 60)
        after = len(self._count_queries(lambda: opacity_leaderboard(seeded)))

        assert after <= before + 1, (
            f"adding 60 members added {after - before} queries -- this is per-member again"
        )

    def test_the_landing_page_headline_is_a_couple_of_queries(self, client, seeded):
        """`/api/insights` built a whole leaderboard to print one percentage."""
        from src.analysis.compliance import late_filing_rate

        self._many_members(seeded, 60)
        queries = self._count_queries(lambda: late_filing_rate(seeded))

        assert len(queries) <= 2, f"{len(queries)} queries for one headline figure"

    def test_the_headline_agrees_with_the_leaderboard(self, seeded):
        """Two code paths, one answer -- or the front page contradicts the table."""
        from src.analysis.compliance import compliance_leaderboard, late_filing_rate

        board = compliance_leaderboard(seeded, min_transactions=1)
        headline = late_filing_rate(seeded)

        assert headline["transactions_checked"] == board["total_transactions_checked"]
        assert headline["filed_late"] == board["total_filed_late"]
        assert headline["late_rate_percent"] == board["overall_late_rate_percent"]
