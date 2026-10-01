"""Donor, lobbying, contract and legislation findings count the member's trades.

Every member-attributed detector counts only trades the member holds -- Self or
Joint -- since #99. These five were never brought under it. Found live:

* Brian Mast "purchased T 22 days before an award": a dependent child's $172
  purchase in a custodial account.
* Greg Steube "traded finance around" three bills: all three Synovus trades,
  and all five counted as his, were his spouse's.

The null model in `significance` builds its own trade streams and has to apply
the same rule, or the q-value is measured against trades no finding could have
been drawn from.
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
    Transaction,
    TransactionType,
)
from tests.test_tier2_detectors import _make_member, _make_trade

EVENT = datetime(2024, 8, 1)


def _trade_owned_by(db, owner, ticker="T"):
    member = _make_member(db, bioguide=f"OW{owner[:6]}{ticker}"[:20])
    txn = _make_trade(db, member, ticker, TransactionType.PURCHASE, EVENT - timedelta(days=10))
    txn.owner = owner
    db.commit()
    return member


def _events(db, member, ticker="T"):
    db.add(GovernmentContract(ticker=ticker, amount=Decimal("5"), awarded_date=EVENT))
    db.add(LobbyingDisclosure(ticker=ticker, registrant="R", filed_date=EVENT))
    db.add(
        CampaignDonation(
            member_id=member.id,
            ticker=ticker,
            donor_name="PAC",
            amount=Decimal("1000"),
            donation_date=EVENT,
        )
    )
    db.commit()


DETECTORS = [detect_contract_front_runs, detect_lobbying_overlaps, detect_donor_conflicts]


@pytest.mark.parametrize("detect", DETECTORS)
@pytest.mark.parametrize("owner", ["Spouse", "Dependent Child"])
def test_someone_elses_trade_is_not_the_members(db_session, detect, owner):
    member = _trade_owned_by(db_session, owner)
    _events(db_session, member)

    assert detect(db_session) == []


@pytest.mark.parametrize("detect", DETECTORS)
@pytest.mark.parametrize("owner", ["Self", "Joint", None])
def test_the_members_own_and_joint_trades_still_count(db_session, detect, owner):
    member = _trade_owned_by(db_session, owner or "")
    if owner is None:
        db_session.query(Transaction).update({"owner": None})
        db_session.commit()
    _events(db_session, member)

    assert len(detect(db_session)) == 1


def test_the_null_model_sees_the_same_trades(db_session):
    from src.analysis.significance import _member_trades, _sector_trades

    spouse = _trade_owned_by(db_session, "Spouse", ticker="LMT")
    own = _trade_owned_by(db_session, "Self", ticker="RTX")

    trades = _member_trades(db_session)
    assert spouse.id not in trades
    assert [t[0] for t in trades[own.id]] == ["RTX"]

    sectors = _sector_trades(db_session)
    assert spouse.id not in sectors
    assert own.id in sectors


def test_the_legislation_detectors_count_only_the_members_trades(db_session):
    from src.analysis.legislation import _member_transactions

    spouse = _trade_owned_by(db_session, "Spouse", ticker="SNV")
    own = _trade_owned_by(db_session, "Self", ticker="JPM")

    by_member = _member_transactions(db_session)
    assert by_member[spouse.id] == []
    assert [t.ticker for t in by_member[own.id]] == ["JPM"]
