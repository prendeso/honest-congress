"""The preloaded identity index must answer exactly what `find_existing` does.

`find_existing` is a round trip per candidate finding, called from both
analyzers' roster walks and from `persist_anomalies`. `stored_by_identity`
replaces it with one query, which is only safe if the two agree on every case
the two partial unique indexes distinguish -- in particular the
`transaction_id IS NULL` clause, without which a member-level finding would be
suppressed by an unrelated finding about one of their trades that happened to
share a title.

That clause is the reason this file exists: it is the easiest part to lose when
turning a query into a dict, and losing it is silent.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.anomaly_key import find_existing, identity_of, stored_by_identity
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
def member(db_session):
    m = Member(
        bioguide_id="IX000001",
        first_name="Index",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
        in_office=True,
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


@pytest.fixture
def trade(db_session, member):
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id="IXDOC",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(disclosure)
    db_session.commit()
    db_session.refresh(disclosure)
    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=datetime(2024, 3, 1),
        transaction_type=TransactionType.PURCHASE,
        description="Big Co",
        ticker="BIG",
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


def _store(db, member, **kwargs):
    row = Anomaly(
        member_id=member.id,
        severity="MEDIUM",
        description="stored",
        **kwargs,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _agree(db, candidate):
    """Both answers for one candidate finding, as (per-row query, index)."""
    key = identity_of(candidate)
    index = stored_by_identity(db)
    return find_existing(db, key), index.get(key)


def test_a_trade_level_finding_is_found_by_both(db_session, member, trade):
    stored = _store(
        db_session,
        member,
        anomaly_type="late_filing",
        title="Late PTR filing",
        transaction_id=trade.id,
    )
    query_answer, index_answer = _agree(
        db_session,
        {
            "member_id": member.id,
            "anomaly_type": "late_filing",
            "transaction_id": trade.id,
            "title": "a completely different title",
        },
    )
    assert query_answer is not None
    assert index_answer is stored
    assert query_answer.id == index_answer.id


def test_a_member_level_finding_is_found_by_both(db_session, member):
    stored = _store(
        db_session,
        member,
        anomaly_type="high_trading_frequency",
        title="Files far more trades than most",
        transaction_id=None,
    )
    query_answer, index_answer = _agree(
        db_session,
        {
            "member_id": member.id,
            "anomaly_type": "high_trading_frequency",
            "title": "Files far more trades than most",
        },
    )
    assert query_answer is not None
    assert index_answer is stored


def test_a_trade_finding_does_not_answer_a_member_level_lookup(db_session, member, trade):
    """The `transaction_id IS NULL` clause, which the index must reproduce.

    A member-level candidate sharing a title with an unrelated trade-level
    finding must come back as *not* stored. Drop that clause and the member-level
    finding is silently suppressed.
    """
    _store(
        db_session,
        member,
        anomaly_type="late_filing",
        title="Same title, different thing",
        transaction_id=trade.id,
    )
    query_answer, index_answer = _agree(
        db_session,
        {
            "member_id": member.id,
            "anomaly_type": "late_filing",
            "title": "Same title, different thing",
        },
    )
    assert query_answer is None
    assert index_answer is None


def test_a_different_member_is_not_a_match(db_session, member):
    _store(
        db_session,
        member,
        anomaly_type="large_trade",
        title="A big one",
        transaction_id=None,
    )
    other = Member(
        bioguide_id="IX000002",
        first_name="Other",
        last_name="Member",
        chamber=Chamber.SENATE,
        party=Party.REPUBLICAN,
        state="TX",
        in_office=True,
    )
    db_session.add(other)
    db_session.commit()

    query_answer, index_answer = _agree(
        db_session,
        {"member_id": other.id, "anomaly_type": "large_trade", "title": "A big one"},
    )
    assert query_answer is None
    assert index_answer is None


def test_a_title_longer_than_the_column_keys_the_same_either_way(db_session, member):
    """Titles are truncated on write, so the key has to be built from the
    truncated value or the two disagree about what a duplicate is."""
    long_title = "L" * 400
    _store(
        db_session,
        member,
        anomaly_type="sector_concentration",
        title=long_title[:200],
        transaction_id=None,
    )
    query_answer, index_answer = _agree(
        db_session,
        {
            "member_id": member.id,
            "anomaly_type": "sector_concentration",
            "title": long_title,
        },
    )
    assert query_answer is not None
    assert (
        index_answer
        is stored_by_identity(db_session)[
            identity_of(
                {
                    "member_id": member.id,
                    "anomaly_type": "sector_concentration",
                    "title": long_title,
                }
            )
        ]
    )


def test_the_index_costs_one_query_whatever_the_population(db_session, member):
    from sqlalchemy import event

    for i in range(25):
        _store(
            db_session,
            member,
            anomaly_type="volume_spikes",
            title=f"Spike {i}",
            transaction_id=None,
        )

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        index = stored_by_identity(db_session)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    assert len(index) >= 25
    assert len(seen) == 1, f"expected one query, got {len(seen)}"
