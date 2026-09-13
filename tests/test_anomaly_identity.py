"""Two findings are the same finding when, and only when, they are.

A single unique index on `(member_id, anomaly_type, title)` enforced
`persist_anomalies`' dedupe key against every writer. The first analysis run
over a populated production database died on it:

    UniqueViolation: duplicate key value violates unique constraint
    "uq_anomaly_member_type_title"
    DETAIL:  Key (member_id, anomaly_type, title)=(12188, late_filing,
    Late PTR filing: severely late (over 3 months)) already exists.

Both rows were correct. A `late_filing` title names a bucket, so a member with
two trades filed more than three months late produces two findings sharing a
title and differing only in `transaction_id`. Enforcing one row per title would
have meant publishing one late trade per member per bucket and dropping the
rest -- data loss dressed as a constraint.

The schema now carries the two identities that are actually true, as partial
unique indexes, and every writer asks `anomaly_key` which one applies.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.analysis import persist_anomalies
from src.analysis.anomaly_key import find_existing, identity_of
from src.db.models import (
    Anomaly,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

# The title from the production failure, verbatim.
BUCKET_TITLE = "Late PTR filing: severely late (over 3 months)"


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="K000001",
        first_name="Key",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db_session.add(m)
    db_session.commit()
    return m


@pytest.fixture
def trades(db_session, member):
    """Two distinct late trades on one filing, as the failing member had."""
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 5, 15),
        document_id="KEY-1",
        is_ptr=True,
    )
    db_session.add(disclosure)
    db_session.commit()

    rows = []
    for day, ticker in ((1, "PEP"), (2, "KO")):
        txn = Transaction(
            disclosure_id=disclosure.id,
            transaction_type=TransactionType.SALE,
            transaction_date=datetime(2023, 11, day),
            description=ticker,
            ticker=ticker,
        )
        db_session.add(txn)
        rows.append(txn)
    db_session.commit()
    return rows


def _late_filing(member_id: int, transaction_id: int | None) -> dict:
    return {
        "member_id": member_id,
        "anomaly_type": "late_filing",
        "severity": "high",
        "title": BUCKET_TITLE,
        "description": "filed late",
        "transaction_id": transaction_id,
    }


class TestIdentityOf:
    def test_a_finding_about_a_trade_is_keyed_by_the_trade(self):
        key = identity_of(_late_filing(1, 77))
        assert key == (1, "late_filing", "transaction", 77)

    def test_a_finding_about_the_member_is_keyed_by_its_title(self):
        key = identity_of(_late_filing(1, None))
        assert key == (1, "late_filing", "title", BUCKET_TITLE)

    def test_two_trades_in_the_same_bucket_are_different_findings(self):
        assert identity_of(_late_filing(1, 77)) != identity_of(_late_filing(1, 78))

    def test_the_same_trade_is_the_same_finding_whatever_the_title_says(self):
        restated = {**_late_filing(1, 77), "title": "Late PTR filing: reworded"}
        assert identity_of(_late_filing(1, 77)) == identity_of(restated)

    def test_an_unkeyable_finding_returns_none(self):
        assert identity_of({"anomaly_type": "late_filing"}) is None
        assert identity_of({"member_id": 1}) is None

    def test_the_title_is_truncated_the_way_the_column_is(self):
        """The key must be built from what is stored, or the in-batch check and
        the database disagree about what a duplicate is."""
        long_title = "x" * 250
        _, _, _, keyed = identity_of({"member_id": 1, "anomaly_type": "t", "title": long_title})
        assert keyed == "x" * 200


class TestTheProductionFailure:
    def test_two_late_trades_in_one_bucket_are_both_stored(self, db_session, member, trades):
        first, second = trades

        inserted = persist_anomalies(
            db_session,
            [_late_filing(member.id, first.id), _late_filing(member.id, second.id)],
        )

        assert inserted == 2, "the second late trade was dropped as a duplicate title"
        stored = db_session.query(Anomaly).filter(Anomaly.anomaly_type == "late_filing").all()
        assert {a.transaction_id for a in stored} == {first.id, second.id}

    def test_the_same_trade_twice_is_still_one_finding(self, db_session, member, trades):
        first = trades[0]

        persist_anomalies(db_session, [_late_filing(member.id, first.id)])
        again = persist_anomalies(db_session, [_late_filing(member.id, first.id)])

        assert again == 0
        assert db_session.query(Anomaly).count() == 1

    def test_the_same_trade_twice_in_one_batch_does_not_break_the_commit(
        self, db_session, member, trades
    ):
        """The autoflush=False trap. The existence check queries the database,
        which cannot see a row added earlier in the same batch."""
        first = trades[0]

        inserted = persist_anomalies(
            db_session,
            [_late_filing(member.id, first.id), _late_filing(member.id, first.id)],
        )

        assert inserted == 1
        assert db_session.query(Anomaly).count() == 1


class TestMemberLevelFindingsAreStillDeduplicated:
    """The guarantee the original index was added for, which must survive."""

    def test_the_same_member_level_finding_twice_is_stored_once(self, db_session, member):
        persist_anomalies(db_session, [_late_filing(member.id, None)])
        again = persist_anomalies(db_session, [_late_filing(member.id, None)])

        assert again == 0
        assert db_session.query(Anomaly).count() == 1

    def test_it_is_deduplicated_within_a_single_batch_too(self, db_session, member):
        inserted = persist_anomalies(
            db_session, [_late_filing(member.id, None), _late_filing(member.id, None)]
        )

        assert inserted == 1

    def test_a_trade_finding_does_not_suppress_a_member_finding(self, db_session, member, trades):
        """`find_existing`'s title branch filters on transaction_id IS NULL, as
        the partial index does. Without that, a member-level finding would be
        skipped because a trade-level one happened to share its title."""
        persist_anomalies(db_session, [_late_filing(member.id, trades[0].id)])

        inserted = persist_anomalies(db_session, [_late_filing(member.id, None)])

        assert inserted == 1, "the member-level finding was suppressed by a trade-level one"
        assert db_session.query(Anomaly).count() == 2


class TestFindExisting:
    def test_it_finds_a_trade_finding_by_its_trade(self, db_session, member, trades):
        persist_anomalies(db_session, [_late_filing(member.id, trades[0].id)])

        found = find_existing(db_session, identity_of(_late_filing(member.id, trades[0].id)))
        assert found is not None
        assert found.transaction_id == trades[0].id

    def test_it_does_not_match_a_different_trade(self, db_session, member, trades):
        persist_anomalies(db_session, [_late_filing(member.id, trades[0].id)])

        assert find_existing(db_session, identity_of(_late_filing(member.id, trades[1].id))) is None
