"""A finding cites the event nearest the trade, and says so after it changes.

One finding is stored per (member, type, trade), and several events can fall in
one trade's window: a large company files lobbying reports through several
registrants every quarter. The survivor was whichever event the table returned
first. Measured on 12 live lobbying findings, 4 cited a farther filing than the
nearest -- Apple at 28 days, graded on that, when Apple's own report was posted
10 days from the trade.

And because persisting a finding only ever inserted, a stored finding kept its
first event for ever even once the detector would cite another.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis import persist_anomalies
from src.analysis.tier2_detectors import detect_lobbying_overlaps
from src.db.models import Anomaly, LobbyingDisclosure, TransactionType
from tests.test_tier2_detectors import _make_member, _make_trade

TRADED = datetime(2024, 11, 19)


def _filing(db, registrant, days_from_trade):
    db.add(
        LobbyingDisclosure(
            ticker="AAPL",
            registrant=registrant,
            filed_date=TRADED + timedelta(days=days_from_trade),
        )
    )
    db.commit()


def test_the_nearest_filing_is_the_one_cited(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "AAPL", TransactionType.SALE, TRADED)
    _filing(db_session, "FAR REGISTRANT", -28)  # inserted first, so it used to win
    _filing(db_session, "APPLE INC.", 10)

    (finding,) = detect_lobbying_overlaps(db_session)

    assert finding["computed_value"] == Decimal("10")
    assert "APPLE INC." in finding["description"]


def test_one_finding_per_trade_whatever_the_number_of_filings(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "AAPL", TransactionType.SALE, TRADED)
    for days in (-25, -5, 3, 20):
        _filing(db_session, f"R{days}", days)

    assert len(detect_lobbying_overlaps(db_session)) == 1


def test_a_stored_finding_is_restated_against_the_nearer_filing(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "AAPL", TransactionType.SALE, TRADED)
    _filing(db_session, "FAR REGISTRANT", -28)
    persist_anomalies(db_session, detect_lobbying_overlaps(db_session))
    (stored,) = db_session.query(Anomaly).all()
    stored.reviewed = True
    db_session.commit()

    # A nearer filing is ingested later.
    _filing(db_session, "APPLE INC.", 10)
    persist_anomalies(db_session, detect_lobbying_overlaps(db_session))

    rows = db_session.query(Anomaly).all()
    assert len(rows) == 1, "restated in place, not inserted beside the old one"
    db_session.refresh(rows[0])
    assert float(rows[0].computed_value) == 10.0
    assert "APPLE INC." in rows[0].description
    assert rows[0].reviewed is True, "a review is not undone by a restatement"


def test_an_unchanged_finding_is_not_rewritten(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "AAPL", TransactionType.SALE, TRADED)
    _filing(db_session, "APPLE INC.", 10)
    persist_anomalies(db_session, detect_lobbying_overlaps(db_session))

    assert persist_anomalies(db_session, detect_lobbying_overlaps(db_session)) == 0
