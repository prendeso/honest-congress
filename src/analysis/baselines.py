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
from typing import Any, Dict, List, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.db.models import (
    Anomaly,
    BillCommittee,
    BillSponsorship,
    CampaignDonation,
    Chamber,
    CommitteeAssignment,
    Disclosure,
    GovernmentContract,
    LobbyingDisclosure,
    Member,
    Transaction,
    TravelPayment,
)

logger = logging.getLogger(__name__)

# Below this many findings of a type, a percentile is noise dressed up as a
# statistic -- 3 rows would make the largest "the 100th percentile".
MIN_POPULATION_FOR_PERCENTILE = 10

# Each detector depends on a table that something else has to populate. When
# that table is empty the detector returns nothing -- which reads identically to
# "checked, found nothing clean". The three Tier-2 tables are the ones this
# matters most for: their feeds (FEC, Senate LDA, USASpending) are separate
# rate-limited commands, so it is entirely possible to run `analyze` against
# tables nothing has filled yet.
DETECTOR_SOURCE_TABLES: Dict[str, Any] = {
    "donor_conflict": CampaignDonation,
    "lobbying_overlap": LobbyingDisclosure,
    "contract_front_run": GovernmentContract,
    "committee_jurisdiction_conflict": CommitteeAssignment,
    "sponsorship_conflict": BillSponsorship,
    "bill_jurisdiction_conflict": BillCommittee,
    "cross_member_cluster": Transaction,
    "large_trade": Transaction,
    "late_filing": Transaction,
    "sector_concentration": Transaction,
    "high_trading_frequency": Transaction,
    "trade_clustering": Transaction,
    "volume_spikes": Transaction,
}


def detectors_without_source_data(db: Session) -> List[Dict[str, str]]:
    """Detectors whose input table is empty, so they cannot produce output.

    Without this, a detector silently returning zero is indistinguishable from
    one that ran and found nothing.
    """
    empty: List[Dict[str, str]] = []
    counts: Dict[str, int] = {}

    for detector, model in DETECTOR_SOURCE_TABLES.items():
        name = model.__tablename__
        if name not in counts:
            counts[name] = db.query(func.count(model.id)).scalar() or 0
        if counts[name] == 0:
            empty.append({"anomaly_type": detector, "empty_source_table": name})

    return sorted(empty, key=lambda e: (e["empty_source_table"], e["anomaly_type"]))


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
        source = DETECTOR_SOURCE_TABLES.get(anomaly_type)
        per_type.append(
            {
                "anomaly_type": anomaly_type,
                "findings": count,
                "members_flagged": flagged_members,
                "member_flag_rate": (
                    round(flagged_members / member_count * 100, 2) if member_count else 0.0
                ),
                # Rows the detector scanned to produce those findings. This is
                # the number that makes a flag count readable: 4 findings out of
                # 1,189 sponsorships says something quite different from 4 out
                # of 4.
                "source_rows_scanned": (
                    db.query(func.count(source.id)).scalar() or 0 if source is not None else None
                ),
            }
        )

    total_findings = sum(findings_by_type)
    detectors_run = len(per_type)
    starved = detectors_without_source_data(db)
    significance = significance_summary(db)
    parsing = parse_quality_summary(db)

    return {
        "members": member_count,
        "detector_types_with_findings": detectors_run,
        # Every (detector, member) pair the suite could have flagged, which is
        # the denominator any statement about how unusual a flag is depends on.
        #
        # This used to be reported alone, under a name that implied it was the
        # test count. It is not, and it understates the burden badly for the
        # event-driven detectors: `sponsorship_conflict` scans every sponsored
        # BILL, not every member -- 1,189 of them across three members on the
        # seeded database, not 3. `source_rows_scanned` per type carries that.
        "member_detector_pairs": detectors_run * member_count,
        "total_findings": total_findings,
        "by_type": per_type,
        # Detectors that could not run at all, as distinct from detectors that
        # ran and found nothing.
        "detectors_without_source_data": starved,
        "significance": significance,
        # Every trade in this project comes out of a PDF. If the parser read
        # the filings badly, nothing downstream of it means anything, so the
        # quality of that read belongs next to the findings.
        "parsing": parsing,
        "caveat": (
            "Findings are pattern matches over public filings, not determinations "
            "of wrongdoing. Thresholds are asserted rather than calibrated; "
            "percentile_rank compares a finding against others of its own type. "
            "A q_value is a false-discovery rate for timing coincidence only, and "
            "a null q_value means no null model exists for that detector -- never "
            "that the finding passed one. Check detectors_without_source_data "
            "before reading an absence of findings as a clean result."
        ),
    }


def significance_summary(db: Session) -> Dict[str, object]:
    """How the findings divide once multiple comparisons are accounted for.

    Reported next to the flag counts because the two are only meaningful
    together: "80 findings" and "6 of them survive correction for the 3,300
    tests that produced them" are very different statements.
    """
    from src.config import get_settings

    alpha = get_settings().fdr_alpha

    tested = db.query(func.count(Anomaly.id)).filter(Anomaly.q_value.isnot(None)).scalar() or 0
    passing = db.query(func.count(Anomaly.id)).filter(Anomaly.q_value <= alpha).scalar() or 0
    untested = db.query(func.count(Anomaly.id)).filter(Anomaly.q_value.is_(None)).scalar() or 0

    return {
        "fdr_alpha": alpha,
        "findings_with_a_null_model": tested,
        "findings_passing_fdr": passing,
        "findings_failing_fdr": tested - passing,
        # Detectors that measure a magnitude rather than a coincidence. They
        # have no null to shuffle, so they carry no q_value -- which is not the
        # same as passing one.
        "findings_without_a_null_model": untested,
        # What an FDR of alpha means you should expect to be wrong among the
        # findings that passed.
        "expected_false_discoveries": round(passing * alpha, 2),
    }


def parse_quality_summary(db: Session) -> Dict[str, object]:
    """How well the filings behind these findings were actually read.

    `parsed` has only ever meant the parser ran without raising. A filing that
    yielded nothing was recorded identically to one read cleanly, which is how
    two filings in the test corpus came to hold no transactions at all without
    anyone noticing.
    """
    parsed = db.query(func.count(Disclosure.id)).filter(Disclosure.parsed.is_(True)).scalar() or 0
    scored = (
        db.query(func.count(Disclosure.id)).filter(Disclosure.parse_confidence.isnot(None)).scalar()
        or 0
    )
    scanned = (
        db.query(func.count(Disclosure.id)).filter(Disclosure.has_text_layer.is_(False)).scalar()
        or 0
    )
    # A filing that had text in it and still yielded nothing. This is the
    # parser's own failure count, and separating the scans out is the only way
    # it means anything: they are 12.7% of the House corpus and they score 0.0
    # by definition.
    empty = (
        db.query(func.count(Disclosure.id))
        .filter(
            Disclosure.parse_confidence == 0.0,
            or_(Disclosure.has_text_layer.is_(None), Disclosure.has_text_layer.is_(True)),
        )
        .scalar()
        or 0
    )
    poor = (
        db.query(func.count(Disclosure.id))
        .filter(Disclosure.parse_confidence.isnot(None), Disclosure.parse_confidence < 0.8)
        .scalar()
        or 0
    )
    average = (
        db.query(func.avg(Disclosure.parse_confidence))
        .filter(Disclosure.parse_confidence.isnot(None))
        .scalar()
    )

    # Schedule H coverage. Reported here because nothing else in the project
    # reports it at all: `detection_summary` is keyed on anomaly TYPES and
    # their `DETECTOR_SOURCE_TABLES`, and travel has no detector, so a backfill
    # storing 700 trips and one storing zero looked identical from every
    # operator-facing surface. That is the shape of defect this file exists to
    # undo -- a number nobody can see is a number nobody can check.
    travel_rows = db.query(func.count(TravelPayment.id)).scalar() or 0
    filings_with_travel = (
        db.query(func.count(func.distinct(TravelPayment.disclosure_id))).scalar() or 0
    )
    # The denominator, and the reason it needs a join. ONLY the House annual
    # form has a Schedule H: Senate annuals are HTML with numbered Parts, and
    # the House candidate, amendment and new-filer variants print A C D E F J
    # with no H at all (see D16). Measured against the whole corpus this would
    # be a ratio nobody could read.
    house_annuals = (
        db.query(func.count(Disclosure.id))
        .join(Member, Member.id == Disclosure.member_id)
        .filter(
            Disclosure.parsed.is_(True),
            Disclosure.is_ptr.is_(False),
            Member.chamber == Chamber.HOUSE,
        )
        .scalar()
        or 0
    )

    return {
        "filings_parsed": parsed,
        # Never scored, because they were parsed before scoring existed. Not
        # the same as scored and fine.
        "filings_never_scored": parsed - scored,
        "mean_confidence": round(float(average), 3) if average is not None else None,
        "filings_below_0_8": poor,
        # The number that says whether the dataset can be trusted at all: a
        # filing that HAD text and the parser still read nothing out of.
        "filings_that_yielded_nothing": empty,
        # Not a parser failure. A scan of a paper form has no text to read, so
        # it scores 0.0 whatever the parser does. Reported on its own because
        # it is a coverage statement about the source -- roughly one House
        # trade report in eight -- and reading it as a bug count is wrong in
        # both directions: it flatters no one and blames the wrong thing.
        "filings_with_no_text_layer": scanned,
        # Privately funded travel, from Schedule H. Sampled at ~52% of House
        # annual reports carrying at least one trip, ~0.9 trips per filing, so
        # a corpus-wide figure far from that is a finding rather than noise.
        "travel_rows": travel_rows,
        "filings_with_travel": filings_with_travel,
        "house_annuals_parsed": house_annuals,
    }
