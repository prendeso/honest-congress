"""Population baselines for anomaly output.

Every threshold in the detector suite is asserted rather than calibrated:
`>100%` appreciation, `>50%` sector concentration, `5+` consecutive trades,
the 90/30/30-day Tier-2 windows. None was derived from the data. That makes
"exceeded threshold 100" hard to defend and easy to dismiss.

Ranking each finding against the population of comparable findings turns the
same number into a statement that stands on its own -- "this member is in the
top 2% of sector concentration among all flagged members" -- without changing
any detector's logic or retuning a single constant.

It also makes the multiple-comparisons problem visible. The suite runs every
detector against every member, so a flag rate needs a denominator to mean
anything. `detection_summary` reports both.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import Anomaly, Member

logger = logging.getLogger(__name__)

# Below this many findings of a type, a percentile is noise dressed up as a
# statistic -- 3 rows would make the largest "the 100th percentile".
MIN_POPULATION_FOR_PERCENTILE = 10


def percentile_rank(value: float, population: Sequence[float]) -> float:
    """Percentage of the population at or below `value`, 0-100.

    Uses the weak definition (<=) so the largest observation ranks 100 and ties
    share a rank.
    """
    if not population:
        return 0.0
    at_or_below = sum(1 for item in population if item <= value)
    return round(at_or_below / len(population) * 100, 2)


def annotate_percentile_ranks(db: Session) -> Dict[str, int]:
    """Rank every anomaly against others of its own type.

    Comparison is within an anomaly type, never across: a `late_filing` measured
    in days and a `large_trade` measured in dollars share no scale.

    Types with too few findings, or whose findings carry no `computed_value`,
    are left unranked rather than given a misleading number.
    """
    ranked = 0
    skipped_small = 0
    skipped_no_value = 0

    types = [row[0] for row in db.query(Anomaly.anomaly_type).distinct().all()]

    for anomaly_type in types:
        rows = (
            db.query(Anomaly)
            .filter(
                Anomaly.anomaly_type == anomaly_type,
                Anomaly.computed_value.isnot(None),
            )
            .all()
        )

        if not rows:
            skipped_no_value += 1
            continue

        if len(rows) < MIN_POPULATION_FOR_PERCENTILE:
            skipped_small += len(rows)
            for row in rows:
                row.percentile_rank = None
            continue

        # computed_value is non-null here: the query filters on it. Bind it to a
        # local so that is visible to the type checker as well as the reader.
        valued = [
            (row, float(row.computed_value)) for row in rows if row.computed_value is not None
        ]
        population = [value for _, value in valued]
        for row, value in valued:
            row.percentile_rank = percentile_rank(value, population)
            ranked += 1

    db.commit()

    logger.info(
        "Percentile ranks: %d ranked, %d in populations too small to rank, "
        "%d types with no computed_value",
        ranked,
        skipped_small,
        skipped_no_value,
    )
    return {
        "ranked": ranked,
        "skipped_small_population": skipped_small,
        "skipped_no_computed_value": skipped_no_value,
    }


def detection_summary(db: Session) -> Dict[str, object]:
    """Context a reader needs to judge the flag list.

    The suite runs every detector against every member. Reporting flags without
    that denominator invites reading a long list as a long list of wrongdoing,
    when some of it is what running many tests over many people produces.
    """
    member_count = db.query(func.count(Member.id)).scalar() or 0

    per_type: List[Dict[str, object]] = []
    findings_by_type: List[int] = []
    for anomaly_type, count in (
        db.query(Anomaly.anomaly_type, func.count(Anomaly.id))
        .group_by(Anomaly.anomaly_type)
        .order_by(func.count(Anomaly.id).desc())
        .all()
    ):
        flagged_members = (
            db.query(func.count(func.distinct(Anomaly.member_id)))
            .filter(Anomaly.anomaly_type == anomaly_type)
            .scalar()
            or 0
        )
        findings_by_type.append(count)
        per_type.append(
            {
                "anomaly_type": anomaly_type,
                "findings": count,
                "members_flagged": flagged_members,
                "member_flag_rate": (
                    round(flagged_members / member_count * 100, 2) if member_count else 0.0
                ),
            }
        )

    total_findings = sum(findings_by_type)
    detectors_run = len(per_type)

    return {
        "members": member_count,
        "detector_types_with_findings": detectors_run,
        # One test per detector per member; the honest denominator for any
        # statement about how unusual a flag is.
        "approximate_tests_run": detectors_run * member_count,
        "total_findings": total_findings,
        "by_type": per_type,
        "caveat": (
            "Findings are pattern matches over public filings, not determinations "
            "of wrongdoing. Thresholds are asserted rather than calibrated; "
            "percentile_rank compares a finding against others of its own type."
        ),
    }
