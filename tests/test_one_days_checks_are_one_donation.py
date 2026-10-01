"""Two checks from one PAC on one day are one donation.

A corporate PAC routinely gives a candidate a primary check and a general check
at once. The detector took whichever row it met first: Elevance Health's PAC was
cited as giving Jared Moskowitz $1,500 on a day it gave him $2,500.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.tier2_detectors import detect_donor_conflicts
from src.db.models import CampaignDonation, TransactionType
from tests.test_tier2_detectors import _make_member, _make_trade

GIVEN = datetime(2024, 3, 31)


def _check(db, member, amount, donor="ELEVANCE HEALTH PAC", when=GIVEN):
    db.add(
        CampaignDonation(
            member_id=member.id,
            ticker="ELV",
            donor_name=donor,
            amount=Decimal(amount),
            donation_date=when,
        )
    )
    db.commit()


def test_same_day_checks_are_summed(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "ELV", TransactionType.PURCHASE, GIVEN - timedelta(days=59))
    _check(db_session, member, "1500")
    _check(db_session, member, "1000")

    (finding,) = detect_donor_conflicts(db_session)

    assert "($2,500)" in finding["description"]


def test_checks_on_different_days_stay_separate_events(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "ELV", TransactionType.PURCHASE, GIVEN - timedelta(days=5))
    _check(db_session, member, "1500")
    _check(db_session, member, "1000", when=GIVEN + timedelta(days=40))

    (finding,) = detect_donor_conflicts(db_session)

    assert finding["computed_value"] == Decimal("5")
    assert "($1,500)" in finding["description"]


def test_the_sentence_does_not_assert_a_conflict(db_session):
    member = _make_member(db_session)
    _make_trade(db_session, member, "ELV", TransactionType.SALE, GIVEN + timedelta(days=3))
    _check(db_session, member, "1000")

    (finding,) = detect_donor_conflicts(db_session)

    assert "regardless of direction" not in finding["description"]
    assert "coincidence" in finding["description"]
