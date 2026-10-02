"""A report filed late is one late filing, however many trades it carries.

Alan Armstrong's single 703-row PTR, filed 113 days after a direct-indexing
account's March 2026 trades, was published as 114 separate late filings, and
ranked him first in the corpus on count alone. The finding is now the filing:
one per late report, keyed to its most delayed trade, saying how many trades it
covers and over what range of delay.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.cli import _SUPERSEDED_WORDING
from tests.test_an_amendment_is_not_a_late_filing import _late, _member, _ptr, _txn

FILED = datetime(2026, 7, 21)


def test_many_late_trades_on_one_report_are_one_finding(db_session):
    member = _member(db_session, "LT00001")
    report = _ptr(db_session, member, "LT-1", FILED)
    for i in range(5):
        _txn(
            db_session,
            report,
            f"Holding {i}",
            "New",
            when=datetime(2026, 3, 27) + timedelta(days=i),
        )

    (finding,) = _late(db_session, member)

    assert finding["late_trades"] == 5
    assert "5 trades" in finding["title"]
    assert "disclosed 5 trades between 112 and 116 days" in finding["description"]
    assert float(finding["computed_value"]) == 116.0, "graded on the filing's worst delay"


def test_two_late_reports_are_two_findings(db_session):
    member = _member(db_session, "LT00002")
    for n, filed in enumerate((FILED, FILED + timedelta(days=30))):
        report = _ptr(db_session, member, f"LT-2-{n}", filed)
        _txn(db_session, report, f"Holding {n}", "New", when=datetime(2026, 3, 27))

    assert len(_late(db_session, member)) == 2


def test_a_single_late_trade_reads_as_one(db_session):
    member = _member(db_session, "LT00003")
    report = _ptr(db_session, member, "LT-3", FILED)
    _txn(db_session, report, "Lone Holding", "New", when=datetime(2026, 3, 27))

    (finding,) = _late(db_session, member)

    assert finding["title"] == "Late PTR filing: significantly late (1-3 months)"
    assert finding["description"].startswith("A trade made on 2026-03-27 was reported 116 days")


def test_household_rows_are_named_and_still_counted(db_session):
    member = _member(db_session, "LT00004")
    report = _ptr(db_session, member, "LT-4", FILED)
    _txn(db_session, report, "Own Holding", "New", when=datetime(2026, 3, 27))
    spouse = _txn(db_session, report, "Spouse Holding", "New", when=datetime(2026, 3, 28))
    spouse.owner = "Spouse"
    db_session.commit()

    (finding,) = _late(db_session, member)

    assert finding["late_trades"] == 2
    assert "1 of the 2 are reported for their spouse" in finding["description"]


def test_the_per_row_findings_already_published_are_purged():
    """Their sentence is one the grouped detector cannot write."""
    assert ("late_filing", "description", "Transaction on ") in _SUPERSEDED_WORDING
