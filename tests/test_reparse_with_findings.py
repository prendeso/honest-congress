"""Re-reading a filing that has findings attached to its trades.

Rebuild run 13, 2026-09-14, one filing out of 744:

    ERROR - Error parsing disclosure 20025799:
    (psycopg.errors.ForeignKeyViolation) update or delete on table
    "transactions" violates foreign key constraint
    "anomalies_transaction_id_fkey" on table "anomalies"
    DETAIL: Key (id)=(36079) is still referenced from table "anomalies".
    [SQL: DELETE FROM transactions WHERE transactions.disclosure_id = ...]

`_clear_parsed_rows` drops a filing's transactions before writing the new read,
because re-reading used to append a second copy of every row. `anomalies` holds
a foreign key to `transactions`, so the delete is refused while any finding
points into it and the whole filing fails to parse.

One filing is not one for long. A filing can only hit this once a finding has
been written about one of its trades, so it spreads as the anomalies table
fills -- and what it breaks is `--min-confidence`, whose entire purpose is to
re-read the corpus after a parser fix.

These tests run on their own SQLite engine with `PRAGMA foreign_keys=ON`.
Without it SQLite does not enforce the constraint at all, the delete succeeds,
and the bug that took down a production parse is invisible to the suite.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    Anomaly,
    Base,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)
from src.ingestion.orchestrator import IngestionOrchestrator


@pytest.fixture
def fk_session():
    """A session where a foreign key actually is one."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enforce(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def _filing_with_a_finding(db):
    member = Member(
        bioguide_id="R000001",
        first_name="Test",
        last_name="Member",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
        in_office=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id="20025799",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)

    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=datetime(2024, 3, 15),
        transaction_type=TransactionType.PURCHASE,
        description="RTX purchase",
        ticker="RTX",
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    db.add(
        Anomaly(
            member_id=member.id,
            anomaly_type="late_filing",
            severity="MEDIUM",
            title="Late filing: RTX",
            transaction_id=txn.id,
            computed_value=Decimal("90"),
            threshold_value=Decimal("45"),
            description="published before this filing was re-read",
        )
    )
    db.commit()
    return disclosure, txn


def test_the_foreign_key_is_enforced_in_this_fixture(fk_session):
    """Otherwise the rest of the file proves nothing.

    SQLite ignores foreign keys unless asked, and the suite's shared engine does
    not ask. A test written against that engine would pass whether or not the
    bug were fixed.
    """
    _, txn = _filing_with_a_finding(fk_session)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        fk_session.query(Transaction).filter(Transaction.id == txn.id).delete(
            synchronize_session=False
        )
        fk_session.commit()
    fk_session.rollback()


def test_a_filing_with_a_finding_can_be_re_read(fk_session):
    disclosure, _ = _filing_with_a_finding(fk_session)

    IngestionOrchestrator._clear_parsed_rows(fk_session, disclosure, [{"ticker": "RTX"}])
    fk_session.commit()

    assert fk_session.query(Transaction).count() == 0


def test_the_finding_goes_with_the_trade_it_was_about(fk_session):
    """Not a loss of information: it was derived from a row being replaced, and
    `analyze` re-derives it from the new read."""
    disclosure, _ = _filing_with_a_finding(fk_session)

    IngestionOrchestrator._clear_parsed_rows(fk_session, disclosure, [{"ticker": "RTX"}])
    fk_session.commit()

    assert fk_session.query(Anomaly).count() == 0


def test_a_read_that_found_nothing_deletes_neither(fk_session):
    """The existing guard, still standing. A failed download or a scan must not
    take out rows a previous parse got right -- nor the findings about them."""
    disclosure, _ = _filing_with_a_finding(fk_session)

    IngestionOrchestrator._clear_parsed_rows(fk_session, disclosure, [])
    fk_session.commit()

    assert fk_session.query(Transaction).count() == 1
    assert fk_session.query(Anomaly).count() == 1


def test_findings_about_other_filings_are_untouched(fk_session):
    """The delete is scoped by the filing being re-read, not by member."""
    disclosure, _ = _filing_with_a_finding(fk_session)
    member = fk_session.query(Member).one()

    other = Disclosure(
        member_id=member.id,
        filing_year=2023,
        filing_type="PTR",
        filing_date=datetime(2023, 6, 1),
        document_id="OTHER",
        is_ptr=True,
        parsed=True,
    )
    fk_session.add(other)
    fk_session.commit()
    fk_session.refresh(other)
    kept = Transaction(
        disclosure_id=other.id,
        transaction_date=datetime(2023, 3, 15),
        transaction_type=TransactionType.PURCHASE,
        description="BA purchase",
        ticker="BA",
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    fk_session.add(kept)
    fk_session.commit()
    fk_session.refresh(kept)
    fk_session.add(
        Anomaly(
            member_id=member.id,
            anomaly_type="late_filing",
            severity="MEDIUM",
            title="Late filing: BA",
            transaction_id=kept.id,
            computed_value=Decimal("90"),
            threshold_value=Decimal("45"),
            description="about a different filing",
        )
    )
    fk_session.commit()

    IngestionOrchestrator._clear_parsed_rows(fk_session, disclosure, [{"ticker": "RTX"}])
    fk_session.commit()

    assert fk_session.query(Transaction).count() == 1
    remaining = fk_session.query(Anomaly).one()
    assert remaining.title == "Late filing: BA"
