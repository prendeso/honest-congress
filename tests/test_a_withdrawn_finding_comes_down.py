"""Correcting a detector does not correct the site.

`persist_anomalies` only ever inserts. Nothing anywhere deletes a finding that
stopped being true. `purge-stale-wording` handles the case where the published
SENTENCE changed and can be matched on; it cannot handle this one, where the
wording is identical and the finding simply should never have existed.

That is exactly what the attribution fix leaves behind. A detector that counted
a spouse's trades as the member's now counts only the member's, so it stops
producing the finding -- and the published one stays up for ever, naming a real
person for trades their own filing says are not theirs. On a 10,594-row corpus
that is 121 of 592 findings, including every trade-derived finding about eight
of the nine members a hostile audit named.

So `retract-withdrawn-findings` re-derives rather than matching text, and the
tests below pin the two guards that stop it deleting real findings.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.extended_anomaly_detector import MIN_CONSECUTIVE_TRADES
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
def cli(monkeypatch, db_session):
    """Point the command's `get_db` at the test session."""
    from contextlib import contextmanager

    import src.cli as cli_module

    @contextmanager
    def _db():
        yield db_session

    monkeypatch.setattr(cli_module, "get_db", _db)
    monkeypatch.setattr(cli_module, "recalculate_member_counts", lambda db: None)
    return cli_module


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="With",
        last_name="Drawn",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="TX",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _streak(db, member, owner):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id=f"WD-{member.bioguide_id}",
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    for i in range(MIN_CONSECUTIVE_TRADES):
        db.add(
            Transaction(
                disclosure_id=d.id,
                transaction_date=datetime(2024, 3, 1) + timedelta(days=i),
                transaction_type=TransactionType.PURCHASE,
                description=f"Withdrawn Holding {i} - Common Stock",
                ticker=f"WD{i}",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
                owner=owner,
            )
        )
    db.commit()
    return d


def _stored(db, member, anomaly_type, title):
    a = Anomaly(
        member_id=member.id,
        anomaly_type=anomaly_type,
        severity="medium",
        title=title,
        description="Published before the detector was corrected.",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _live_member(db, bioguide):
    """A member whose own streak keeps the re-derivation non-empty.

    Load-bearing in every test that expects a deletion or a non-deletion for a
    reason OTHER than the empty-run guard. Without one, `produced` is empty,
    the command aborts, nothing is deleted, and the test passes whatever the
    code does -- which is how three mutations survived: removing the held-type
    skip, deleting every type instead of the covered ones, and making
    `--dry-run` delete anyway.
    """
    member = _member(db, bioguide)
    _streak(db, member, "Self")
    return member


def _remaining(db, member):
    return db.query(Anomaly).filter(Anomaly.member_id == member.id).all()


class TestARetractionTakesDownWhatIsNoLongerTrue:
    def test_a_finding_the_detector_stopped_producing_is_deleted(self, cli, db_session):
        member = _member(db_session, "WD00001")
        _streak(db_session, member, "Spouse")
        _stored(
            db_session,
            member,
            "trade_clustering",
            "Consecutive same-direction trades (5 in a row)",
        )
        # A second member whose finding IS still derived, so the run is not
        # empty. Without one this hits the "produced nothing at all" guard and
        # passes for the wrong reason -- which is how it first failed.
        _live_member(db_session, "WD00001L")

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=False))

        assert _remaining(db_session, member) == []

    def test_a_finding_the_detector_still_produces_survives(self, cli, db_session):
        # The other half. A retraction that deletes everything is not a
        # retraction, it is an outage.
        member = _member(db_session, "WD00002")
        _streak(db_session, member, "Self")

        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        live = [
            a
            for a in ExtendedAnomalyDetector().detect_trade_timing_anomalies(db_session)
            if a["member_id"] == member.id and a["anomaly_type"] == "trade_clustering"
        ]
        assert len(live) == 1, "fixture must produce a finding for this test to mean anything"
        _stored(db_session, member, "trade_clustering", live[0]["title"])

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=False))

        assert len(_remaining(db_session, member)) == 1

    def test_dry_run_deletes_nothing(self, cli, db_session):
        member = _member(db_session, "WD00003")
        _streak(db_session, member, "Spouse")
        _stored(
            db_session,
            member,
            "trade_clustering",
            "Consecutive same-direction trades (5 in a row)",
        )
        _live_member(db_session, "WD00003L")

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=True))

        assert len(_remaining(db_session, member)) == 1

    def test_a_type_it_does_not_cover_is_never_touched(self, cli, db_session):
        # `late_filing` is produced by a path this command does re-run, but a
        # type outside the covered tuple must be invisible to it either way.
        member = _member(db_session, "WD00004")
        _streak(db_session, member, "Spouse")
        _stored(db_session, member, "donor_conflict", "Some other detector's finding")
        _live_member(db_session, "WD00004L")

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=False))

        assert [a.anomaly_type for a in _remaining(db_session, member)] == ["donor_conflict"]


class TestTheGuardsAgainstDeletingRealFindings:
    def test_a_run_that_produces_nothing_deletes_nothing(self, cli, db_session, monkeypatch):
        # An empty result is indistinguishable from a broken run, and the safe
        # reading of a detector suite that found nothing is that something is
        # wrong with the suite -- not that every published finding is false.
        member = _member(db_session, "WD00005")
        _stored(
            db_session,
            member,
            "trade_clustering",
            "Consecutive same-direction trades (9 in a row)",
        )

        import src.analysis.extended_anomaly_detector as ext
        from src.analysis.trade_analyzer import TradeAnalyzer

        monkeypatch.setattr(
            ext.ExtendedAnomalyDetector, "detect_trade_timing_anomalies", lambda self, db: []
        )
        monkeypatch.setattr(TradeAnalyzer, "analyze_member", lambda self, db, mid: [])

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=False))

        assert len(_remaining(db_session, member)) == 1

    def test_a_held_type_is_skipped_entirely(self, cli, db_session, monkeypatch):
        # A held detector does not run, so it produces nothing. Reading that as
        # "withdrawn" would delete every finding it ever wrote.
        member = _member(db_session, "WD00006")
        _stored(
            db_session,
            member,
            "trade_clustering",
            "Consecutive same-direction trades (7 in a row)",
        )
        _live_member(db_session, "WD00006L")

        import src.analysis as analysis

        monkeypatch.setattr(
            analysis, "detector_is_disabled", lambda atype: atype == "trade_clustering"
        )

        cli.cmd_retract_withdrawn_findings(Namespace(dry_run=False))

        assert len(_remaining(db_session, member)) == 1


def test_every_covered_type_is_one_this_command_re_runs():
    """A type whose detector is not re-run here would be deleted wholesale."""
    from src.cli import _ATTRIBUTED_TO_THE_MEMBER

    produced_by_the_detectors_this_reruns = {
        # TradeAnalyzer.analyze_member
        "high_trading_frequency",
        "large_trade",
        "sector_concentration",
        "late_filing",
        # ExtendedAnomalyDetector.detect_trade_timing_anomalies
        "trade_clustering",
        "volume_spikes",
        # run_committee_conflict_detection
        "committee_jurisdiction_conflict",
    }
    missing = set(_ATTRIBUTED_TO_THE_MEMBER) - produced_by_the_detectors_this_reruns
    assert not missing, f"covered but never re-derived, so always deleted: {sorted(missing)}"
