"""Eight filings are published under a man who died in 1972.

`.first()` on a query with no ORDER BY picked arbitrarily between two House
members sharing a surname and a state, and in production it picked wrong:

    B000315  Nicholas Begich, D-AK    disappeared October 1972, declared dead
             -> disclosure_count 8, including a 2026 PTR for $100,001-$250,000
    B001323  Nicholas Begich III, R-AK, sitting
             -> disclosure_count 0

Fixing the matcher does not fix those rows, and this is the part that is easy
to assume away: re-running `ingest` cannot repair them either, because
`_already_queued` short-circuits on `document_id` before the matcher is ever
consulted. Confirmed against production AFTER the corrected matcher ran a full
ingest -- B000315 still held 8 and B001323 still held 0.

Hence a separate pass. It re-reads the Clerk index, re-runs today's matcher,
and reports every disagreement. What these tests pin is mostly what it REFUSES
to do: it moves only a filing resolving to exactly one member, it writes
nothing without --apply, and it never touches a row it cannot decide.
"""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest

from src.db.models import Chamber, Disclosure, Member, Party


def member(db, bioguide, first, last, state, district=None, in_office=True):
    row = Member(
        bioguide_id=bioguide,
        first_name=first,
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state=state,
        district=district,
        in_office=in_office,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def filing(db, member_row, document_id, year=2026, filing_type="P"):
    row = Disclosure(
        member_id=member_row.id,
        filing_year=year,
        filing_type=filing_type,
        filing_date=datetime(year, 2, 2),
        document_id=document_id,
        document_url=f"https://example.invalid/{document_id}.pdf",
        is_ptr=filing_type == "P",
        parsed=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def index_entry(first, last, state, document_id, district="00", suffix="", year=2026):
    return {
        "first_name": first,
        "last_name": last,
        "state": state,
        "district": district,
        "suffix": suffix,
        "document_id": document_id,
        "filing_year": year,
        "filing_type": "PTR",
    }


def run(db, entries, apply=False, years=(2026,), capsys=None):
    """Drive the command with a stubbed Clerk index and the test's session."""
    from src import cli

    @contextmanager
    def _fake_get_db():
        yield db

    with (
        patch.object(cli, "get_db", _fake_get_db),
        patch(
            "src.ingestion.house.HouseIngester.fetch_ptr_xml_index",
            lambda self, year: [e for e in entries if e["filing_year"] == year],
        ),
        patch("src.ingestion.house.HouseIngester.fetch_annual_xml_index", lambda self, year: []),
    ):
        cli.cmd_repair_house_attribution(Namespace(years=list(years), apply=apply))

    return capsys.readouterr().out if capsys else ""


@pytest.fixture
def begich(db_session):
    """Both Begiches and the eight filings, as production holds them."""
    grandfather = member(db_session, "B000315", "Nicholas", "Begich", "AK", in_office=False)
    sitting = member(db_session, "B001323", "Nicholas", "Begich", "AK", in_office=True)

    docs = [f"2000{n}" for n in range(1, 9)]
    for doc in docs:
        filing(db_session, grandfather, doc)

    return grandfather, sitting, docs


class TestTheFilingsMoveToTheLivingMember:
    def test_apply_moves_all_eight(self, db_session, begich):
        grandfather, sitting, docs = begich
        entries = [index_entry("Nicholas", "Begich", "AK", d, suffix="III") for d in docs]

        run(db_session, entries, apply=True)

        owners = {
            d.member_id
            for d in db_session.query(Disclosure).filter(Disclosure.document_id.in_(docs))
        }
        assert owners == {sitting.id}

    def test_without_apply_nothing_is_written(self, db_session, begich, capsys):
        grandfather, _sitting, docs = begich
        entries = [index_entry("Nicholas", "Begich", "AK", d, suffix="III") for d in docs]

        output = run(db_session, entries, apply=False, capsys=capsys)

        still_his = (
            db_session.query(Disclosure).filter(Disclosure.member_id == grandfather.id).count()
        )
        assert still_his == 8
        assert "8 filing(s) would move" in output
        assert "--apply" in output

    def test_the_report_names_both_men_and_says_which_is_sitting(self, db_session, begich, capsys):
        _grandfather, _sitting, docs = begich
        entries = [index_entry("Nicholas", "Begich", "AK", d, suffix="III") for d in docs]

        output = run(db_session, entries, capsys=capsys)

        assert "B000315" in output and "former" in output
        assert "B001323" in output and "sitting" in output
        assert "III" in output, "the suffix is the only thing separating them by name"


class TestItRefusesWhatItCannotDecide:
    def test_a_filer_matching_nobody_is_reported_and_left_alone(self, db_session, capsys):
        sitting = member(db_session, "W000001", "Joe", "Wilson", "SC", "02")
        stored = filing(db_session, sitting, "30001")
        entries = [index_entry("Hampton", "Redmond", "SC", "30001", district="02")]

        output = run(db_session, entries, apply=True, capsys=capsys)

        db_session.refresh(stored)
        assert stored.member_id == sitting.id
        assert "cannot decide" in output.lower()

    def test_a_correct_attribution_is_confirmed_not_rewritten(self, db_session, capsys):
        sitting = member(db_session, "M001218", "Rich", "McCormick", "GA", "07")
        stored = filing(db_session, sitting, "30002")
        # Stale Clerk district: his PTRs carry GA06 against a roster GA-7.
        entries = [index_entry("Richard Dean Dr", "McCormick", "GA", "30002", district="06")]

        output = run(db_session, entries, apply=True, capsys=capsys)

        db_session.refresh(stored)
        assert stored.member_id == sitting.id
        assert "Attribution the matcher confirms: 1" in output
        assert "Attribution it would change:      0" in output

    def test_a_stored_filing_absent_from_the_index_is_reported_not_moved(self, db_session, capsys):
        """Candidate reports ("C") are the real case: 24 were stored under the
        old matcher and the corrected index parser excludes them, so they have
        no index row to re-derive an attribution from. Deleting them is a
        separate decision; this pass only says how many there are."""
        sitting = member(db_session, "B000002", "Cori", "Bush", "MO", "01")
        stored = filing(db_session, sitting, "30003", filing_type="C")
        # A real index alongside it, so this exercises "absent from a working
        # index" and not the outage guard, which the next test covers.
        other = filing(db_session, sitting, "30013")
        entries = [index_entry("Cori", "Bush", "MO", "30013", district="01")]

        output = run(db_session, entries, apply=True, capsys=capsys)

        db_session.refresh(other)
        assert other.member_id == sitting.id

        db_session.refresh(stored)
        assert stored.member_id == sitting.id
        assert "not in the index" in output
        assert "C=1" in output

    def test_an_empty_index_concludes_nothing(self, db_session, capsys):
        """A Clerk outage returns []. Treating that as "every filing is
        unattributable" would be the worst possible reading of it."""
        sitting = member(db_session, "B000003", "Ann", "Other", "TX", "01")
        stored = filing(db_session, sitting, "30004")

        with patch("src.ingestion.house.HouseIngester.fetch_ptr_xml_index", lambda self, year: []):
            output = run(db_session, [], apply=True, capsys=capsys)

        db_session.refresh(stored)
        assert stored.member_id == sitting.id
        assert "refusing to conclude" in output


class TestWhatRidesAlong:
    def test_transactions_follow_the_filing_without_being_touched(self, db_session, begich):
        """`Transaction` carries `disclosure_id` and no `member_id`, so moving
        the filing moves its trades. This asserts that rather than assuming it."""
        from decimal import Decimal

        from src.db.models import Transaction, TransactionType

        grandfather, sitting, docs = begich
        stored = db_session.query(Disclosure).filter(Disclosure.document_id == docs[0]).one()
        txn = Transaction(
            disclosure_id=stored.id,
            transaction_date=datetime(2026, 1, 15),
            transaction_type=TransactionType.PURCHASE,
            description="Apple Inc Common Stock",
            ticker="AAPL",
            amount_min=Decimal("100001"),
            amount_max=Decimal("250000"),
        )
        db_session.add(txn)
        db_session.commit()
        txn_id = txn.id

        entries = [index_entry("Nicholas", "Begich", "AK", d, suffix="III") for d in docs]
        run(db_session, entries, apply=True)

        db_session.refresh(stored)
        db_session.refresh(txn)
        assert stored.member_id == sitting.id
        assert txn.id == txn_id and txn.disclosure_id == stored.id

    def test_the_report_counts_what_moves_with_each_filing(self, db_session, begich, capsys):
        _grandfather, _sitting, docs = begich
        entries = [index_entry("Nicholas", "Begich", "AK", d, suffix="III") for d in docs]

        output = run(db_session, entries, capsys=capsys)

        assert "transaction(s)" in output
        assert "asset(s)" in output
