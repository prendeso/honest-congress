"""The three Tier-2 detectors each closed on a claim the data cannot carry.

Published under members' names on the live site:

    contract_front_run  "Buying ahead of a public contract award is one of the
                         clearest insider-information signals available."
    donor_conflict      "... raise conflict-of-interest concerns regardless of
                         direction."
    lobbying_overlap    "The issuer is actively trying to shape federal policy
                         around the time of the trade."

The third was also wrong on its facts. 69% of the 1,092 live lobbying findings
sit on a date within days of a quarterly LDA deadline -- the date a REPORT was
due, not a date anyone lobbied. And not one of them had p <= 0.05 against the
shifted-calendar null.

The corrected sentences say what the bill detectors already say: a disclosed
coincidence in time. These tests hold each detector to that, and hold the
purge to removing what the old code wrote.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.tier2_detectors import (
    detect_contract_front_runs,
    detect_donor_conflicts,
    detect_lobbying_overlaps,
)
from src.db.models import (
    CampaignDonation,
    GovernmentContract,
    LobbyingDisclosure,
    TransactionType,
)
from tests.test_tier2_detectors import _make_member, _make_trade

OLD = {
    "contract_front_run": "clearest insider-information signals",
    "donor_conflict": "raise conflict-of-interest concerns",
    "lobbying_overlap": "actively trying to shape federal policy",
}

WHEN = datetime(2024, 6, 1)


def _donor(db):
    member = _make_member(db, "CO00001")
    db.add(
        CampaignDonation(
            member_id=member.id,
            ticker="AAPL",
            donor_name="Apple Inc",
            amount=Decimal("5000"),
            donation_date=WHEN,
        )
    )
    db.commit()
    _make_trade(db, member, "AAPL", TransactionType.PURCHASE, WHEN + timedelta(days=5))
    return detect_donor_conflicts(db)


def _lobbying(db):
    member = _make_member(db, "CO00002")
    db.add(LobbyingDisclosure(ticker="LMT", registrant="Lockheed Martin", filed_date=WHEN))
    db.commit()
    _make_trade(db, member, "LMT", TransactionType.PURCHASE, WHEN + timedelta(days=5))
    return detect_lobbying_overlaps(db)


def _contract(db):
    member = _make_member(db, "CO00003")
    db.add(
        GovernmentContract(
            ticker="RTX", agency="Department of Defense", amount=Decimal("1e8"), awarded_date=WHEN
        )
    )
    db.commit()
    _make_trade(db, member, "RTX", TransactionType.PURCHASE, WHEN - timedelta(days=5))
    return detect_contract_front_runs(db)


DETECTORS = {
    "donor_conflict": _donor,
    "lobbying_overlap": _lobbying,
    "contract_front_run": _contract,
}


@pytest.mark.parametrize("kind", sorted(DETECTORS))
def test_the_finding_calls_itself_a_coincidence(db_session, kind):
    (finding,) = DETECTORS[kind](db_session)
    assert "coincidence in time" in finding["description"]


@pytest.mark.parametrize("kind", sorted(DETECTORS))
def test_the_old_claim_is_one_the_detector_can_no_longer_write(db_session, kind):
    (finding,) = DETECTORS[kind](db_session)
    assert OLD[kind] not in finding["description"]


def test_the_lobbying_finding_says_the_date_is_a_deadline(db_session):
    (finding,) = _lobbying(db_session)
    assert "reporting deadline" in finding["description"]


@pytest.mark.parametrize("kind", sorted(OLD))
def test_the_purge_removes_what_the_old_code_wrote(kind):
    from src.cli import _SUPERSEDED_WORDING

    assert (kind, "description", OLD[kind]) in _SUPERSEDED_WORDING
