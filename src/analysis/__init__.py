"""Analysis package: anomaly detectors and shared helpers."""

import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.analysis.advanced_anomaly_detector import (
    AdvancedAnomalyDetector,
    run_advanced_anomaly_detection,
)
from src.analysis.anomaly_key import identity_of, stored_by_identity
from src.analysis.clustering import (
    detect_cross_member_clusters,
    run_cluster_detection,
)
from src.analysis.committee_conflicts import (
    detect_committee_jurisdiction_conflicts,
    run_committee_conflict_detection,
)
from src.analysis.extended_anomaly_detector import (
    ExtendedAnomalyDetector,
    run_extended_anomaly_detection,
)
from src.analysis.tier2_detectors import (
    detect_contract_front_runs,
    detect_donor_conflicts,
    detect_lobbying_overlaps,
    run_tier2_detection,
)
from src.analysis.trade_analyzer import TradeAnalyzer, analyze_trades
from src.analysis.wealth_analyzer import WealthAnalyzer, analyze_wealth
from src.db.models import Anomaly, Transaction, normalize_severity

logger = logging.getLogger(__name__)


def transaction_amount(txn: Transaction) -> float:
    """Return a single representative dollar amount for a transaction.

    Transactions store an `amount_min`/`amount_max` range. We use the midpoint
    when both are present, otherwise whichever side is set, otherwise 0.
    """
    if txn.amount_min is not None and txn.amount_max is not None:
        return float((txn.amount_min + txn.amount_max) / 2)
    if txn.amount_max is not None:
        return float(txn.amount_max)
    if txn.amount_min is not None:
        return float(txn.amount_min)
    return 0.0


# Severity normalization lives on the model (src/db/models.py) so it applies to
# every write path, not just this one. Re-exported here for existing callers.
_normalize_severity = normalize_severity


def _build_title(a: Dict[str, Any]) -> str:
    """Generate a sensible title for an anomaly dict that lacks one."""
    atype = a.get("anomaly_type", "anomaly")
    if atype == "wealth_vs_salary":
        years = a.get("years", "")
        suffix = f" ({years})" if years else ""
        return f"Wealth growth far exceeds salary{suffix}"
    if atype == "rapid_asset_appreciation":
        asset = (a.get("asset_description") or "asset")[:60]
        start, end = a.get("start_year"), a.get("end_year")
        return f"Rapid appreciation: {asset} ({start}-{end})"
    if atype == "outperforming_trades":
        year = a.get("year", "")
        return f"Trading returns outperformed market benchmark ({year})"
    if atype == "trade_clustering":
        count = a.get("count", "")
        return f"Consecutive same-direction trades ({count} in a row)"
    if atype == "perfect_timing":
        rate = a.get("success_rate", 0)
        return f"Suspiciously high trade success rate ({rate:.0f}%)"
    if atype == "sector_concentration":
        sectors = ", ".join(a.get("sectors", [])[:3]) or "regulated sectors"
        return f"Heavy concentration in {sectors}"
    if atype == "loss_avoidance":
        rate = a.get("avoidance_rate", 0)
        return f"Loss-avoidance pattern ({rate:.0f}% rate)"
    if atype == "multi_factor_risk":
        count = a.get("anomaly_count", 0)
        return f"Multi-factor risk: {count} different anomaly types"
    if atype == "volume_spikes":
        n = a.get("spike_count", 0)
        return f"Unusual trading volume spikes ({n})"
    return atype.replace("_", " ").title()


def members_with_annual_filings(db, minimum: int = 2):
    """Members with at least `minimum` annual (FD) filings, as Member rows.

    Two detectors open with `db.query(Member).all()` and then skip any member
    with fewer than two FD disclosures. The roster is every member in history --
    12,770 rows -- so the skip fires about 12,400 times, each after a query that
    had to cross the network to find out.

    Asking the database the same question once is behaviour-preserving by
    construction: the members this returns are exactly the ones that got past
    the `continue`. Same shape as the scoping in `WealthAnalyzer` and
    `TradeAnalyzer`, which were fixed for the same reason.
    """
    from sqlalchemy import func

    from src.analysis.wealth_analyzer import net_worth_snapshot_clause
    from src.db.models import Disclosure, Member

    # `filing_type == "FD"` matched ZERO of the 3,900 stored rows. Only
    # `senate.py` ever wrote "FD", as a fallback for when it could not read a
    # report title off the page, and it stopped firing once real titles were
    # stored -- the House never used it at all. So this returned [], and BOTH
    # detectors that call it (advanced_anomaly_detector.py:134 and :387) walked
    # an empty roster and found nothing, silently, for months.
    #
    # `wealth_vs_salary` sitting at 0 findings looked like a fact about
    # Congress. It was a fact about this line.
    #
    # The replacement asks what the filing IS rather than matching a label no
    # ingester writes, and shares its definition with the net-worth series so
    # the two cannot disagree about what an annual filing is.
    comparable = (
        db.query(Disclosure.member_id)
        .filter(net_worth_snapshot_clause())
        .group_by(Disclosure.member_id)
        .having(func.count(Disclosure.id) >= minimum)
    )
    member_ids = [row[0] for row in comparable]
    return db.query(Member).filter(Member.id.in_(member_ids)).all() if member_ids else []


def members_who_traded(db):
    """Members with at least one disclosed transaction, as Member rows.

    The trade-timing detector skips any member with no trades, which is all but
    a few hundred of the roster. Retired members are deliberately included:
    membership of this set is decided by having traded, not by being in office.
    """
    from src.db.models import Disclosure, Member, Transaction

    traded = db.query(Disclosure.member_id).join(
        Transaction, Transaction.disclosure_id == Disclosure.id
    )
    member_ids = [row[0] for row in traded.distinct()]
    return db.query(Member).filter(Member.id.in_(member_ids)).all() if member_ids else []


def detector_is_disabled(anomaly_type: str) -> bool:
    """Whether a detector's output would be thrown away if it ran.

    `persist_anomalies` already refuses to store a disabled type, and
    `detect_red_flag_combinations` already refuses to count one. What neither
    does is stop the detector RUNNING, and three of them walk the full roster
    with per-member queries.

    Measured on one production rebuild: stock outperformance took 17m15s to
    produce 23 findings that were then dropped, and loss avoidance 17m17s to
    produce 18 more. Thirty-four minutes of a 168-minute analysis step, spent
    computing rows the project has already judged unfit to publish.

    Checking here rather than at each call site keeps the single source of truth
    in `Settings.disabled_anomaly_types_set`.
    """
    from src.config import get_settings

    return anomaly_type in get_settings().disabled_anomaly_types_set


def persist_anomalies(db: Session, anomalies: List[Dict[str, Any]]) -> int:
    """Persist anomaly dicts to the database.

    Each dict must have at least `member_id` and `anomaly_type`. Dicts without
    a `member_id` are skipped (they can't be attached to a member).

    Deduplicates by (member_id, anomaly_type, title). Commits at the end.
    Returns the number of newly inserted rows.

    Retried once if the database connection dies on that commit. The analysis
    step held a connection for 92 minutes in the run of 2026-09-14, and a drop
    at the end of a detector would otherwise throw away everything it found and
    take the step with it -- a detector that took a quarter of an hour to
    produce its findings, lost to a dropped packet while writing them.

    There is no cache to rebuild here, which is what makes the retry simple:
    `seen` is local to each attempt and rebuilt by re-running, and the pending
    rows the rollback expunged are re-created the same way. The existence check
    reads committed state, so it is correct on the second pass without help.
    """
    from src.db.resilience import CONNECTION_LOSS_RETRIES, commit_or_recover

    for _attempt in range(1 + CONNECTION_LOSS_RETRIES):
        inserted = _add_anomalies(db, anomalies)
        # Nothing new to write, so nothing to lose: the old shape skipped the
        # commit in this case and there is no reason to start issuing one.
        if not inserted:
            return 0
        if commit_or_recover(db, rebuild_caches=lambda: None, unit="anomalies"):
            return inserted
    logger.warning(
        "The database connection dropped on every attempt to store %d finding(s); "
        "they are not saved, and the next analysis run re-derives them",
        len(anomalies),
    )
    return 0


def _add_anomalies(db: Session, anomalies: List[Dict[str, Any]]) -> int:
    """Add the rows, without committing. Safe to call again after a rollback."""
    from src.config import get_settings

    disabled = get_settings().disabled_anomaly_types_set

    inserted = 0
    skipped_disabled = 0
    # See src/analysis/anomaly_key.py for what counts as the same finding. The
    # existence check below queries the database, which cannot see rows added
    # earlier in this same batch and not yet flushed (SessionLocal is
    # autoflush=False) -- so track them here too, or a batch containing the same
    # anomaly twice fails the whole commit.
    seen: set[tuple] = set()
    # One query for everything already stored, rather than one per candidate.
    # `seen` is still needed for the in-batch case: rows added below are not
    # committed, and autoflush=False means a query would not see them either.
    stored = stored_by_identity(db)
    for a in anomalies:
        member_id = a.get("member_id")
        anomaly_type = a.get("anomaly_type")
        if not member_id or not anomaly_type:
            continue

        # Backstop for detectors whose output is not defensible. Gating here as
        # well as at the call sites means a disabled type cannot reach the
        # database even if a new caller forgets to check.
        if anomaly_type in disabled:
            skipped_disabled += 1
            continue

        title = a.get("title") or _build_title(a)
        severity = _normalize_severity(a.get("severity"))
        description = a.get("description") or title

        key = identity_of(a, title=title)
        if key is None or key in seen:
            continue

        if key in stored:
            continue

        seen.add(key)

        db.add(
            Anomaly(
                member_id=member_id,
                anomaly_type=anomaly_type,
                severity=severity,
                title=title[:200],
                description=description,
                disclosure_id=a.get("disclosure_id"),
                transaction_id=a.get("transaction_id"),
                computed_value=a.get("computed_value"),
                threshold_value=a.get("threshold_value"),
            )
        )
        inserted += 1

    if skipped_disabled:
        logger.info(
            "Skipped %d anomalies of disabled types (%s)",
            skipped_disabled,
            ", ".join(sorted(disabled)),
        )

    return inserted


__all__ = [
    "WealthAnalyzer",
    "analyze_wealth",
    "TradeAnalyzer",
    "analyze_trades",
    "AdvancedAnomalyDetector",
    "run_advanced_anomaly_detection",
    "ExtendedAnomalyDetector",
    "run_extended_anomaly_detection",
    "detect_committee_jurisdiction_conflicts",
    "run_committee_conflict_detection",
    "detect_cross_member_clusters",
    "run_cluster_detection",
    "detect_donor_conflicts",
    "detect_lobbying_overlaps",
    "detect_contract_front_runs",
    "run_tier2_detection",
    "transaction_amount",
    "persist_anomalies",
]
