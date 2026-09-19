"""`"severity": "MEDIUM"` was a literal, and said nothing about the finding.

Measured on a real corpus, finding 567 sat at EXACTLY its threshold -- 5 of a
required 5 -- at the **9th percentile** of its own type, and was published as

    severity: medium

in the same words as a run of 229. A hostile audit raised the framing in 146 of
the 174 findings it examined. Most of that is opinion; this part is a defect: a
word that grades a finding has to be a function of the finding.

`percentile_rank` is the number to grade on, and `baselines.py` already says
why -- thresholds here "are asserted rather than calibrated, so 'top 2% of
findings of this type' is a far more defensible statement than 'exceeded
threshold 100'".

So severity is derived in the pass that computes the rank, which is the only
place that holds the population it is ranked against.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.analysis.baselines import (
    HIGH_PERCENTILE,
    MEDIUM_PERCENTILE,
    annotate_percentile_ranks,
    severity_from_percentile,
)
from src.db.models import Anomaly, Chamber, Member, Party


class TestTheGradeIsAFunctionOfTheRank:
    @pytest.mark.parametrize("rank", [100.0, 99.0, 95.0, HIGH_PERCENTILE])
    def test_the_top_tenth_is_high(self, rank):
        assert severity_from_percentile(rank) == "high"

    @pytest.mark.parametrize("rank", [89.9, 75.0, MEDIUM_PERCENTILE])
    def test_the_upper_half_is_medium(self, rank):
        assert severity_from_percentile(rank) == "medium"

    @pytest.mark.parametrize("rank", [49.9, 9.46, 0.0])
    def test_everything_else_is_low(self, rank):
        assert severity_from_percentile(rank) == "low"

    def test_finding_567s_rank_is_not_medium(self):
        """The case that prompted this: threshold exactly met, 9th percentile,
        graded the same as a finding four times more extreme."""
        assert severity_from_percentile(9.46) == "low"
        assert severity_from_percentile(9.46) != severity_from_percentile(98.9)


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="Sev",
        last_name="Erity",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="OR",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _anomaly(db, member, value, severity="medium", anomaly_type="high_trading_frequency"):
    a = Anomaly(
        member_id=member.id,
        anomaly_type=anomaly_type,
        severity=severity,
        title=f"Graded finding {value}",
        description="x",
        computed_value=Decimal(str(value)),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


class TestTheStoredGradeIsRewritten:
    def test_the_least_extreme_finding_is_regraded_down(self, db_session):
        member = _member(db_session, "SV00001")
        # Twenty findings, all published "medium" by the detector.
        rows = [_anomaly(db_session, member, v) for v in range(11, 31)]

        annotate_percentile_ranks(db_session)
        for row in rows:
            db_session.refresh(row)

        lowest = min(rows, key=lambda r: float(r.computed_value))
        highest = max(rows, key=lambda r: float(r.computed_value))

        assert lowest.severity == "low"
        assert highest.severity == "high"
        assert lowest.severity != highest.severity, (
            "a finding at the bottom of its type cannot be graded like one at the top"
        )

    def test_a_population_too_small_to_rank_keeps_the_detectors_grade(self, db_session):
        """An unrankable finding is not evidence of a mild one.

        Overwriting it with a grade nothing supports would be the same
        invention this replaces.
        """
        member = _member(db_session, "SV00002")
        row = _anomaly(db_session, member, 12, severity="high", anomaly_type="donor_conflict")

        annotate_percentile_ranks(db_session)
        db_session.refresh(row)

        assert row.percentile_rank is None
        assert row.severity == "high"

    def test_the_run_reports_how_many_it_regraded(self, db_session):
        member = _member(db_session, "SV00003")
        for v in range(40, 60):
            _anomaly(db_session, member, v, anomaly_type="volume_spikes")

        result = annotate_percentile_ranks(db_session)

        assert result["regraded"] >= 20
