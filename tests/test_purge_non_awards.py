"""Findings already written when a deobligation counted as an award.

`persist_anomalies` only ever inserts. Merging `award_action_criteria` stops
the detector producing these, and stops nothing that is already in the table
and being served to visitors under a named member's page.

The repair does not read the description text, and it does not reconstruct the
award date arithmetically either. It applies the detector's own condition: some
row that *is* an award, under today's criteria, for this ticker, with the trade
inside the window the finding recorded. Deletions cannot be undone by re-running,
so the test that decides them is the detector's, not an inference about it --
see `test_a_trade_timestamped_mid_afternoon_is_not_deleted` for the inference
that was tried first and would have deleted true findings.
"""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.cli import cmd_purge_non_awards
from src.db.models import (
    Anomaly,
    Chamber,
    Disclosure,
    GovernmentContract,
    Member,
    Party,
    Transaction,
    TransactionType,
)

AWARDED = datetime(2024, 8, 1)
DAYS_BEFORE = 14


@pytest.fixture
def run_purge(db_session):
    @contextmanager
    def _fake_get_db():
        yield db_session

    def _run(dry_run: bool = False):
        with patch("src.cli.get_db", _fake_get_db):
            cmd_purge_non_awards(Namespace(dry_run=dry_run))

    return _run


def _member(db, bioguide="M000001") -> Member:
    m = Member(
        bioguide_id=bioguide,
        first_name="Test",
        last_name="Member",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
        in_office=True,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _trade(db, member: Member, ticker: str) -> Transaction:
    when = AWARDED - timedelta(days=DAYS_BEFORE)
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=when.year,
        filing_type="PTR",
        filing_date=when,
        document_id=f"DOC_{member.id}_{ticker}",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)
    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=TransactionType.PURCHASE,
        description=f"{ticker} test",
        ticker=ticker,
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _finding(db, member: Member, txn: Transaction) -> Anomaly:
    anomaly = Anomaly(
        member_id=member.id,
        anomaly_type="contract_front_run",
        severity="HIGH",
        title=f"Contract front-run: bought {txn.ticker} {DAYS_BEFORE}d before award",
        transaction_id=txn.id,
        computed_value=Decimal(str(DAYS_BEFORE)),
        threshold_value=Decimal("30"),
        description="published while a deobligation counted as an award",
    )
    db.add(anomaly)
    db.commit()
    db.refresh(anomaly)
    return anomaly


def _stored(db) -> int:
    return db.query(Anomaly).filter(Anomaly.anomaly_type == "contract_front_run").count()


def test_a_finding_whose_award_was_a_deobligation_is_deleted(db_session, run_purge):
    member = _member(db_session)
    txn = _trade(db_session, member, "ORCL")
    _finding(db_session, member, txn)
    db_session.add(
        GovernmentContract(ticker="ORCL", amount=Decimal("-5313059"), awarded_date=AWARDED)
    )
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 0


def test_a_finding_backed_by_a_real_award_survives(db_session, run_purge):
    member = _member(db_session, bioguide="K000001")
    txn = _trade(db_session, member, "RTX")
    _finding(db_session, member, txn)
    db_session.add(
        GovernmentContract(ticker="RTX", amount=Decimal("5000000"), awarded_date=AWARDED)
    )
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 1


def test_a_real_award_on_the_same_day_as_a_deobligation_keeps_the_finding(db_session, run_purge):
    """The finding was never wrong; only one of the two rows behind it was."""
    member = _member(db_session, bioguide="S000001")
    txn = _trade(db_session, member, "ORCL")
    _finding(db_session, member, txn)
    db_session.add_all(
        [
            GovernmentContract(ticker="ORCL", amount=Decimal("-5313059"), awarded_date=AWARDED),
            GovernmentContract(ticker="ORCL", amount=Decimal("250000"), awarded_date=AWARDED),
        ]
    )
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 1


def test_a_zero_dollar_modification_does_not_support_a_finding(db_session, run_purge):
    member = _member(db_session, bioguide="Z000001")
    txn = _trade(db_session, member, "NOC")
    _finding(db_session, member, txn)
    db_session.add(GovernmentContract(ticker="NOC", amount=Decimal("0"), awarded_date=AWARDED))
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 0


def test_dry_run_deletes_nothing(db_session, run_purge):
    member = _member(db_session, bioguide="D000001")
    txn = _trade(db_session, member, "ORCL")
    _finding(db_session, member, txn)
    db_session.add(
        GovernmentContract(ticker="ORCL", amount=Decimal("-5313059"), awarded_date=AWARDED)
    )
    db_session.commit()

    run_purge(dry_run=True)
    assert _stored(db_session) == 1


def test_a_trade_timestamped_mid_afternoon_is_not_deleted(db_session, run_purge):
    """The regression that decided how this command identifies a finding.

    The first version reconstructed the award date as the trade date plus the
    `computed_value` days the detector recorded. That is exact only while every
    date in both tables is midnight. Give the trade a time of day and the
    subtraction behind `computed_value` truncates: 13 days and 11 hours records
    as 13, the reconstruction lands a day early, no award is found there, and a
    true finding about a named person is deleted with no way to get it back.

    Here the award is real and the trade is genuinely inside the window. It must
    survive.
    """
    member = _member(db_session, bioguide="T000001")
    when = AWARDED - timedelta(days=DAYS_BEFORE) + timedelta(hours=13)
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=when.year,
        filing_type="PTR",
        filing_date=when,
        document_id="DOC_AFTERNOON",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(disclosure)
    db_session.commit()
    db_session.refresh(disclosure)
    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=TransactionType.PURCHASE,
        description="RTX test",
        ticker="RTX",
        amount_min=Decimal("1000"),
        amount_max=Decimal("15000"),
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)

    # 13, not 14: (AWARDED - when) is 13 days and 11 hours, and `.days` truncates.
    anomaly = Anomaly(
        member_id=member.id,
        anomaly_type="contract_front_run",
        severity="HIGH",
        title="Contract front-run: bought RTX 13d before award",
        transaction_id=txn.id,
        computed_value=Decimal("13"),
        threshold_value=Decimal("30"),
        description="a real award, recorded from a trade with a time of day",
    )
    db_session.add(anomaly)
    db_session.add(
        GovernmentContract(ticker="RTX", amount=Decimal("5000000"), awarded_date=AWARDED)
    )
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 1


def test_an_award_outside_the_window_does_not_support_the_finding(db_session, run_purge):
    """Support means the detector would flag this trade, not that the ticker has awards."""
    member = _member(db_session, bioguide="W000001")
    txn = _trade(db_session, member, "ORCL")
    _finding(db_session, member, txn)
    db_session.add_all(
        [
            GovernmentContract(ticker="ORCL", amount=Decimal("-5313059"), awarded_date=AWARDED),
            # Real, but a year later: no trade in this window precedes it.
            GovernmentContract(
                ticker="ORCL",
                amount=Decimal("250000"),
                awarded_date=AWARDED + timedelta(days=365),
            ),
        ]
    )
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 0


def test_the_window_comes_from_the_finding_not_from_todays_default(db_session, run_purge):
    """A finding written under a narrower window is judged by that window.

    Judging it by whatever the default happens to be today would delete findings
    that were correct when produced, or keep ones that never were.
    """
    member = _member(db_session, bioguide="V000001")
    txn = _trade(db_session, member, "GD")  # 14 days before the award
    anomaly = _finding(db_session, member, txn)
    anomaly.threshold_value = Decimal("7")  # narrower than the 14 days actually elapsed
    db_session.add(GovernmentContract(ticker="GD", amount=Decimal("5000000"), awarded_date=AWARDED))
    db_session.commit()

    run_purge()
    assert _stored(db_session) == 0


def test_other_anomaly_types_are_never_touched(db_session, run_purge):
    """The purge reasons about awards, so it has nothing to say about anything else."""
    member = _member(db_session, bioguide="L000001")
    txn = _trade(db_session, member, "ORCL")
    db_session.add(
        Anomaly(
            member_id=member.id,
            anomaly_type="late_filing",
            severity="MEDIUM",
            title="Late filing",
            transaction_id=txn.id,
            computed_value=Decimal("90"),
            threshold_value=Decimal("45"),
            description="unrelated",
        )
    )
    db_session.add(
        GovernmentContract(ticker="ORCL", amount=Decimal("-5313059"), awarded_date=AWARDED)
    )
    db_session.commit()

    run_purge()
    assert db_session.query(Anomaly).filter(Anomaly.anomaly_type == "late_filing").count() == 1
