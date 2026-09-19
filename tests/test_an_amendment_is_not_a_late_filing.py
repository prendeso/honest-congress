"""The House PTR marks every row "New" or "Amended". We read the label and
threw the answer away.

Rep. William Keating disclosed a 13 September 2023 sale on PTR 20023752,
digitally signed 28 September -- 15 days, comfortably inside the STOCK Act's
45. Document 20023767, filed 2024-01-16, restates that row and one other, and
its own text says so:

    2000111429 SIMON PPTY GROUP LP NOTE S 09/13/2023 09/25/2023 $15,001 -
    FILING STATUS: Amended
    ...
    UNITED STATES TREAS BILLS      P 12/29/2023 01/16/2024 $1,001 - $15,000
    FILING STATUS: New

Scoring the amended copy against the original trade date published "Late PTR
filing: significantly late (1-3 months)", 125 days, about a member who filed in
15.

`restatements.py` catches this when the two rows agree on content. They need
not. Rep. Laurel Lee's re-filing writes "2000114315 SP Alibaba Group Holding
Limited" where the original wrote "SP Alibaba Group Holding Limited S
(partial)", so the content keys miss and a trade reported SIX DAYS after it
happened was published as 591 days late. The form saying "Amended" needs no
matching at all.

The labels render with their small-caps glyphs as NUL bytes, so once those are
stripped the footnote reads "F S: Amended" -- which `_FOOTNOTE_PREFIX` already
recognised, and already discarded.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.analysis.trade_analyzer import TradeAnalyzer
from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType
from src.parsing.ptr_parser import AMENDED, NEW, PTRParser, _filing_status_in

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestReadingTheLabel:
    def test_the_footnote_is_read_through_the_nul_bytes(self):
        # Exactly as pdfplumber returns it: small caps come back as NULs, so
        # "Filing Status" arrives as "F\x00\x00\x00\x00\x00 S\x00\x00\x00\x00\x00".
        raw = "F\x00\x00\x00\x00\x00 S\x00\x00\x00\x00\x00: Amended"
        assert _filing_status_in(raw) == AMENDED

    def test_new_is_read_too(self):
        assert _filing_status_in("F\x00\x00\x00\x00\x00 S\x00\x00\x00\x00\x00: New") == NEW

    def test_a_row_with_no_footnote_says_nothing(self):
        assert _filing_status_in("Apple Inc. (AAPL) [ST] P 01/02/2024 $1,001 - $15,000") is None
        assert _filing_status_in("") is None
        assert _filing_status_in(None) is None

    def test_a_status_it_does_not_recognise_is_not_guessed(self):
        # The safe direction: an unread status leaves the row scored as it is
        # today rather than silently excused from the deadline.
        assert _filing_status_in("F S: Withdrawn") is None

    def test_another_footnote_is_not_mistaken_for_it(self):
        # "Subholding Of" and "Description" render as "S O:" and "D:".
        assert _filing_status_in("S\x00\x00\x00\x00 O\x00: Bill's IRA") is None
        assert _filing_status_in("D\x00\x00\x00\x00: BOEING CO NOTE") is None

    def test_a_location_footnote_beginning_new_is_not_a_filing_status(self):
        """The label has to be anchored, not just the value recognised.

        "Location" renders as "L:", and a filing whose location is New York,
        New Jersey or New Orleans puts the word "New" straight after a
        footnote colon. A looser pattern reads that as `FILING STATUS: New`
        and marks a restated row as a first disclosure -- which is the failure
        this whole file exists to prevent, in reverse.
        """
        assert _filing_status_in("L\x00\x00\x00\x00\x00\x00\x00\x00: New York, NY") is None
        assert _filing_status_in("L: New Orleans, LA") is None
        assert _filing_status_in("S O: New Amsterdam Trust") is None


class TestTheRealDocuments:
    """Anchored on the two filings the finding was wrong about.

    Captured the way `tests/fixtures/ptr/` captures every other filing: the
    pdfplumber tables and text, not the PDF, so the fixture is diffable and the
    repo stays small.

    These go through `_parse_tables`, not `_parse_table_row`, because the
    footnote block is a SEPARATE ROW from the record it describes. The status
    is carried onto the transaction above it inside that loop, and a test that
    called the row parser directly would never see it.
    """

    AMENDMENT = "20023767"
    ORIGINAL = "20023752"

    def _parse(self, document_id):
        import json

        path = FIXTURES / "ptr" / f"{document_id}.json"
        if not path.exists():
            pytest.skip(f"{document_id} not vendored")
        filing = json.loads(path.read_text())
        from src.parsing.ptr_parser import ParseQuality

        return PTRParser()._parse_tables(filing["tables"], ParseQuality())

    def test_the_amendment_marks_the_restated_rows_and_only_those(self):
        rows = self._parse(self.AMENDMENT)

        assert len(rows) == 3
        statuses = [t.get("filing_status") for t in rows]
        assert statuses == [AMENDED, AMENDED, NEW], (
            "Boeing and Simon Property are restatements; the Treasury bill is "
            "the one genuinely new row in that document"
        )

    def test_the_original_filing_marks_everything_new(self):
        rows = self._parse(self.ORIGINAL)

        assert rows
        assert all(t.get("filing_status") == NEW for t in rows)


# ---------------- what the detector does with it ----------------


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="Will",
        last_name="Amend",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="MA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _ptr(db, member, doc, filed):
    d = Disclosure(
        member_id=member.id,
        filing_year=filed.year,
        filing_type="PTR",
        filing_date=filed,
        document_id=doc,
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _txn(db, disclosure, description, status, when=datetime(2023, 9, 13)):
    t = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=TransactionType.SALE,
        description=description,
        ticker=None,
        amount_min=Decimal("15001"),
        amount_max=Decimal("50000"),
        owner="Self",
        filing_status=status,
    )
    db.add(t)
    db.commit()
    return t


def _late(db, member):
    return TradeAnalyzer()._check_late_filings(db, member.id, member)


class TestAnAmendedRowIsNotScoredForLateness:
    def test_keatings_case(self, db_session):
        """The amendment alone, with content the restatement guard cannot
        match -- which is Laurel Lee's shape, and the reason the label is
        needed at all."""
        member = _member(db_session, "AM00001")
        amendment = _ptr(db_session, member, "20023767", datetime(2024, 1, 16))
        _txn(db_session, amendment, "2000111429 SIMON PPTY GROUP LP NOTE", AMENDED)

        assert _late(db_session, member) == [], (
            "125 days measured against a trade the member reported in 15"
        )

    def test_a_new_row_in_the_same_amendment_is_still_scored(self, db_session):
        """The other half. The Treasury bill in that same document is the one
        row genuinely disclosed for the first time, and it is late or not on
        its own merits."""
        member = _member(db_session, "AM00002")
        amendment = _ptr(db_session, member, "20023767b", datetime(2024, 6, 16))
        _txn(db_session, amendment, "UNITED STATES TREAS BILLS", NEW, when=datetime(2023, 12, 29))

        found = _late(db_session, member)
        assert len(found) == 1
        assert found[0]["anomaly_type"] == "late_filing"

    def test_a_row_the_form_did_not_label_is_scored_as_before(self, db_session):
        """NULL is most of the corpus -- every row stored before this, and
        every Senate row, since eFD's HTML carries no such field. Excusing
        those from the deadline would delete real late filings wholesale."""
        member = _member(db_session, "AM00003")
        filing = _ptr(db_session, member, "20023999", datetime(2024, 6, 16))
        _txn(db_session, filing, "Some Asset - Common Stock", None)

        assert len(_late(db_session, member)) == 1

    def test_the_original_timely_filing_is_never_flagged_either_way(self, db_session):
        member = _member(db_session, "AM00004")
        original = _ptr(db_session, member, "20023752", datetime(2023, 9, 28))
        _txn(db_session, original, "828807DF1 [CS]", NEW)

        assert _late(db_session, member) == [], "15 days is inside the 45-day cap"
