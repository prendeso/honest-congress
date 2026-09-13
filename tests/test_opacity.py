"""Disclosure opacity.

Measures how legibly a member files, not what they did. It is reported because
it bounds what every other detector can see: a member whose filings cannot be
read will show few findings for reasons unrelated to their trading, and a reader
should not mistake that for a clean record.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.analysis.opacity import (
    MIN_ITEMS_FOR_SCORE,
    _is_vague,
    member_opacity,
    opacity_leaderboard,
)
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


class TestVagueness:
    @pytest.mark.parametrize(
        "description",
        [None, "", "   ", "various", "VARIOUS", "N/A", "n/a", "see attached", "unknown", "x"],
    )
    def test_uninformative_descriptions_are_vague(self, description):
        assert _is_vague(description) is True

    @pytest.mark.parametrize(
        "description",
        ["Apple Inc", "Various Industries Inc", "Vanguard 500 Index Fund", "US Treasury Note"],
    )
    def test_real_names_are_not_vague(self, description):
        # "Various Industries Inc" contains a vague token but names a company.
        assert _is_vague(description) is False


@pytest.fixture
def db():
    session = SessionLocal()
    for table in (Transaction, Asset, Disclosure, Member):
        session.query(table).delete()
    session.commit()
    yield session
    session.close()


def _member(db, bioguide="OP00001", last="Filer"):
    m = Member(
        bioguide_id=bioguide,
        first_name="Op",
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="AK",
        in_office=True,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _disclosure(db, member, doc_id, parsed=True):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 5, 1),
        document_id=doc_id,
        is_ptr=True,
        parsed=parsed,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _txn(db, disclosure, ticker="AAPL", description="Apple Inc", amounts=True):
    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 3, 1),
            transaction_type=TransactionType.PURCHASE,
            description=description,
            ticker=ticker,
            amount_min=Decimal("1001") if amounts else None,
            amount_max=Decimal("15000") if amounts else None,
        )
    )
    db.commit()


class TestMemberOpacity:
    def test_clean_filings_score_zero(self, db):
        m = _member(db)
        d = _disclosure(db, m, "CLEAN")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d)

        score = member_opacity(db, m)

        assert score["opacity_score"] == 0.0
        assert score["components"]["transactions_missing_ticker"] == 0

    def test_missing_tickers_raise_the_score(self, db):
        m = _member(db)
        d = _disclosure(db, m, "NOTICKER")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d, ticker=None)

        score = member_opacity(db, m)

        assert score["opacity_score"] > 0
        assert score["components"]["transactions_missing_ticker_percent"] == 100.0

    def test_vague_descriptions_raise_the_score(self, db):
        m = _member(db)
        d = _disclosure(db, m, "VAGUE")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d, description="various")

        score = member_opacity(db, m)

        assert score["components"]["items_vaguely_described_percent"] == 100.0

    def test_unreadable_amounts_raise_the_score(self, db):
        m = _member(db)
        d = _disclosure(db, m, "NOAMOUNT")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d, amounts=False)

        score = member_opacity(db, m)

        assert score["components"]["items_with_unreadable_amount_percent"] == 100.0

    def test_unparsed_disclosures_raise_the_score(self, db):
        m = _member(db)
        d = _disclosure(db, m, "UNPARSED", parsed=False)
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d)

        score = member_opacity(db, m)

        assert score["components"]["disclosures_unparsed"] == 1
        assert score["components"]["filings_unreadable_percent"] == 100.0
        assert score["opacity_score"] > 0

    def test_worst_case_scores_higher_than_partial(self, db):
        clean = _member(db, "OP00002", "Clean")
        d1 = _disclosure(db, clean, "C1")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d1)

        opaque = _member(db, "OP00003", "Opaque")
        d2 = _disclosure(db, opaque, "O1", parsed=False)
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d2, ticker=None, description="various", amounts=False)

        assert (
            member_opacity(db, opaque)["opacity_score"] > member_opacity(db, clean)["opacity_score"]
        )

    def test_too_few_items_is_not_scored(self, db):
        m = _member(db)
        d = _disclosure(db, m, "TINY")
        _txn(db, d)

        assert member_opacity(db, m) is None

    def test_member_with_no_disclosures_is_not_scored(self, db):
        assert member_opacity(db, _member(db)) is None


class TestOpacityLeaderboard:
    def test_least_legible_ranks_first(self, db):
        clean = _member(db, "OP00004", "Clean")
        d1 = _disclosure(db, clean, "LC1")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d1)

        opaque = _member(db, "OP00005", "Opaque")
        d2 = _disclosure(db, opaque, "LO1", parsed=False)
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d2, ticker=None, description="n/a", amounts=False)

        board = opacity_leaderboard(db)

        assert board["members"][0]["member_name"].endswith("Opaque")
        assert board["members_scored"] == 2

    def test_note_disclaims_a_conduct_reading(self, db):
        note = opacity_leaderboard(db)["note"]

        assert "not their conduct" in note
        assert "filing system rather" in note


class TestOpacityApi:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_leaderboard_endpoint(self, db, client):
        m = _member(db)
        d = _disclosure(db, m, "API1")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d, ticker=None)

        r = client.get("/api/compliance/opacity/")

        assert r.status_code == 200
        assert r.json()["members_scored"] == 1

    def test_opacity_path_is_not_shadowed_by_the_member_route(self, db, client):
        """`/opacity/` must not be parsed as a member id.

        It shares a prefix with `/api/compliance/{member_id}`, so it only
        resolves correctly because it is declared first.
        """
        r = client.get("/api/compliance/opacity/")

        assert r.status_code == 200
        assert "members_scored" in r.json()

    def test_member_endpoint(self, db, client):
        m = _member(db)
        d = _disclosure(db, m, "API2")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d, description="various")

        r = client.get(f"/api/compliance/opacity/{m.id}")

        assert r.status_code == 200
        assert r.json()["components"]["items_vaguely_described_percent"] == 100.0

    def test_unscoreable_member_is_404(self, db, client):
        m = _member(db)

        assert client.get(f"/api/compliance/opacity/{m.id}").status_code == 404


class TestAScannedFilerIsNotInvisible:
    """The worst case used to fall out of the measure entirely.

    A scan of a paper form is `parsed = True` -- the parser ran, raised
    nothing, and extracted nothing -- so the old `not d.parsed` measure scored
    it 0% unparsed, and because it produced no transactions or assets it put
    nothing in the item denominator either. A member filing exclusively on
    paper therefore appeared perfectly legible, and if they had no readable
    filings at all they fell below MIN_ITEMS_FOR_SCORE and vanished from the
    leaderboard: the least legible filer in Congress, excluded from the
    legibility ranking for being too illegible.

    12.7% of 2024-25 House PTRs are such scans, so this is not hypothetical.
    """

    def _scan(self, db, member, doc_id):
        d = _disclosure(db, member, doc_id)
        d.has_text_layer = False
        d.parse_confidence = 0.0
        db.commit()
        return d

    def test_a_filer_with_only_scans_is_scored_at_all(self, db):
        m = _member(db)
        for i in range(3):
            self._scan(db, m, f"SCAN-{i}")

        score = member_opacity(db, m)

        assert score is not None, "a member whose every filing is unreadable must be scored"
        assert score["components"]["disclosures_scanned"] == 3

    def test_a_filer_with_only_scans_is_the_most_opaque(self, db):
        """Not 25 out of 100 -- which is what averaging in three undefined zeroes gave."""
        scanner = _member(db, "OP00010", "Scanner")
        for i in range(3):
            self._scan(db, scanner, f"S-{i}")

        clean = _member(db, "OP00011", "Clean")
        d = _disclosure(db, clean, "CLEAN-1")
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, d)

        assert member_opacity(db, scanner)["opacity_score"] == 100.0
        assert member_opacity(db, clean)["opacity_score"] == 0.0

    def test_scans_dilute_a_members_score_in_proportion(self, db):
        """Half unreadable is not the same as all unreadable."""
        m = _member(db)
        readable = _disclosure(db, m, "READ-1")
        readable.parse_confidence = 1.0
        db.commit()
        for _ in range(MIN_ITEMS_FOR_SCORE):
            _txn(db, readable)
        self._scan(db, m, "SCAN-X")

        score = member_opacity(db, m)

        assert score["components"]["filings_unreadable_percent"] == 50.0
        # One of four components at 50, the other three at 0.
        assert score["opacity_score"] == 12.5

    def test_a_zero_confidence_parse_counts_even_with_a_text_layer(self, db):
        """A filing that had text and still yielded nothing is unreadable too."""
        m = _member(db)
        broken = _disclosure(db, m, "BROKEN-1")
        broken.has_text_layer = True
        broken.parse_confidence = 0.0
        db.commit()

        score = member_opacity(db, m)

        assert score is not None
        assert score["components"]["filings_unreadable"] == 1
        assert score["components"]["disclosures_scanned"] == 0
