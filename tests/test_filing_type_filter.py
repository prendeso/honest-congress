"""Selecting "P - Periodic Report" returned Senate annual reports.

`filing_type` was matched with `ilike("%...%")`. House filing types are SINGLE
LETTERS, and the Senate does not store a code at all -- it stores the filing's
title, one variant per calendar year and amendment:

    'PTR'  'O'  'X'  'A'  'C'  'H'  'T'  'G'  'B'  'W'  'D'  'E'
    'Annual Report for CY 2025'
    'Annual Report for CY 2024 (Amendment 1)'
    'Candidate Report  (Amendment 2)'
    'New Filer Report for 01/21/2025'
    ... 33 distinct values in all

A substring match over that reads one as the other. Measured live:
`?filing_type=P` returned 2,085 rows -- the 1,724 PTRs plus ~361 Senate annual
reports, because "Report" contains a "p". `?filing_type=D` swept up anything
with a "d" in it.

The page's own dropdown was hand-written and had drifted the other way: it
offered `FD`, which matches nothing in the database, and omitted `D`, `W`, `B`,
`E` and all nineteen Senate values -- so a third of the corpus was unreachable
through the filter while a dead option sat in the menu. That is the drift
`src/analysis/catalog.py` was written to end, so the vocabulary is now served
from the data.

These values are not tidy and the endpoint reports them untidy. Normalising the
Senate's titles into codes belongs at ingest, where it happens once and is
recorded -- not in a display layer inventing a code the filter would then fail
to match. That is left undone and stated rather than papered over.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.database import SessionLocal
from src.db.models import Chamber, Disclosure, Member, Party

# Real values, taken from the live database.
STORED = [
    ("PTR", 3),
    ("O", 2),
    ("D", 1),
    ("Annual Report for CY 2025", 2),
    ("Annual Report for CY 2024 (Amendment 1)", 1),
    ("Candidate Report  (Amendment 2)", 1),
]


@pytest.fixture
def seeded():
    session = SessionLocal()
    session.query(Disclosure).delete()
    session.query(Member).delete()
    session.commit()

    member = Member(
        bioguide_id="FT00001",
        first_name="Filing",
        last_name="Type",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    session.add(member)
    session.commit()

    n = 0
    for value, count in STORED:
        for _ in range(count):
            n += 1
            session.add(
                Disclosure(
                    member_id=member.id,
                    filing_year=2025,
                    filing_type=value,
                    filing_date=datetime(2025, 6, 1),
                    document_id=f"FT-{n}",
                    document_url=f"https://example.invalid/{n}.pdf",
                    is_ptr=value == "PTR",
                    parsed=True,
                )
            )
    session.commit()

    yield session
    session.query(Disclosure).delete()
    session.query(Member).delete()
    session.commit()
    session.close()


def total(client, query):
    return client.get(f"/api/disclosures?page_size=1&{query}").json()["total"]


class TestTheFilterIsExact:
    def test_a_single_letter_does_not_match_a_title_containing_it(self, seeded):
        """The live defect. "P" is not in any stored value as a code here, yet
        it matched 2,085 rows -- every PTR, and every Senate row with a "p"
        anywhere in its title."""
        client = TestClient(app)

        assert total(client, "filing_type=P") == 0

    def test_ptr_returns_only_ptrs(self, seeded):
        client = TestClient(app)

        assert total(client, "filing_type=PTR") == 3

    def test_d_does_not_sweep_up_everything_containing_a_d(self, seeded):
        """'D' as a substring appears in "Candidate Report" and "Amendment"."""
        client = TestClient(app)

        assert total(client, "filing_type=D") == 1

    def test_a_senate_title_matches_itself_exactly(self, seeded):
        client = TestClient(app)

        assert total(client, "filing_type=Annual Report for CY 2025") == 2

    def test_a_senate_title_does_not_match_its_own_amendment(self, seeded):
        """ "Annual Report for CY 2024" is a prefix of the (Amendment 1) value,
        so a substring match conflated a filing with its own correction."""
        client = TestClient(app)

        assert total(client, "filing_type=Annual Report for CY 2024") == 0
        assert total(client, "filing_type=Annual Report for CY 2024 (Amendment 1)") == 1

    def test_case_and_surrounding_space_are_forgiven(self, seeded):
        client = TestClient(app)

        assert total(client, "filing_type=ptr") == 3
        assert total(client, "filing_type= PTR ") == 3

    def test_a_type_nobody_stores_returns_nothing_rather_than_noise(self, seeded):
        """`FD` sat in the dropdown and matched nothing. Under the substring
        match a wrong option was indistinguishable from an empty result; now
        both are honestly empty, and the option is gone."""
        client = TestClient(app)

        assert total(client, "filing_type=FD") == 0


class TestTheVocabularyComesFromTheData:
    def test_every_stored_type_is_listed_with_its_count(self, seeded):
        client = TestClient(app)

        body = client.get("/api/disclosures/filing-types").json()
        counts = {t["filing_type"]: t["count"] for t in body["filing_types"]}

        assert counts == dict(STORED)

    def test_it_is_ordered_by_how_common_each_one_is(self, seeded):
        client = TestClient(app)

        listed = [
            t["count"] for t in client.get("/api/disclosures/filing-types").json()["filing_types"]
        ]

        assert listed == sorted(listed, reverse=True)

    def test_every_listed_type_actually_filters_to_its_own_count(self, seeded):
        """The endpoint and the filter have to agree, or the menu offers options
        that return nothing. This is the assertion the hand-written list failed."""
        client = TestClient(app)

        for entry in client.get("/api/disclosures/filing-types").json()["filing_types"]:
            from urllib.parse import quote

            got = total(client, f"filing_type={quote(entry['filing_type'])}")
            assert got == entry["count"], entry

    def test_the_page_no_longer_carries_its_own_list(self):
        from pathlib import Path

        html = (
            Path(__file__).resolve().parents[1] / "src" / "templates" / "disclosures.html"
        ).read_text()

        assert 'value="FD"' not in html, "the dead option is still in the menu"
        assert "/api/disclosures/filing-types" in html
