"""Analysis package: anomaly detectors and shared helpers."""

import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.analysis.advanced_anomaly_detector import (
    AdvancedAnomalyDetector,
    run_advanced_anomaly_detection,
)
from src.analysis.extended_anomaly_detector import (
    ExtendedAnomalyDetector,
    run_extended_anomaly_detection,
)
from src.analysis.performance_analyzer import PerformanceAnalyzer, analyze_performance
from src.analysis.tier2_detectors import (
    detect_contract_front_runs,
    detect_donor_conflicts,
    detect_lobbying_overlaps,
    run_tier2_detection,
)
from src.analysis.trade_analyzer import TradeAnalyzer, analyze_trades
from src.analysis.wealth_analyzer import WealthAnalyzer, analyze_wealth
from src.db.models import Anomaly, Transaction

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


_API_SEVERITIES = {"low", "medium", "high"}


def _normalize_severity(severity: Any) -> str:
    """Map any detector's severity into the API vocabulary (low/medium/high)."""
    if severity is None:
        return "medium"
    if isinstance(severity, int):
        if severity >= 8:
            return "high"
        if severity >= 5:
            return "medium"
        return "low"
    s = str(severity).lower()
    if s == "critical":
        return "high"
    return s if s in _API_SEVERITIES else "medium"


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


def persist_anomalies(db: Session, anomalies: List[Dict[str, Any]]) -> int:
    """Persist anomaly dicts to the database.

    Each dict must have at least `member_id` and `anomaly_type`. Dicts without
    a `member_id` are skipped (they can't be attached to a member).

    Deduplicates by (member_id, anomaly_type, title). Commits at the end.
    Returns the number of newly inserted rows.
    """
    from src.config import get_settings

    disabled = get_settings().disabled_anomaly_types_set

    inserted = 0
    skipped_disabled = 0
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

        existing = (
            db.query(Anomaly)
            .filter(
                Anomaly.member_id == member_id,
                Anomaly.anomaly_type == anomaly_type,
                Anomaly.title == title,
            )
            .first()
        )
        if existing:
            continue

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

    if inserted:
        db.commit()
    return inserted


__all__ = [
    "WealthAnalyzer",
    "analyze_wealth",
    "TradeAnalyzer",
    "analyze_trades",
    "PerformanceAnalyzer",
    "analyze_performance",
    "AdvancedAnomalyDetector",
    "run_advanced_anomaly_detection",
    "ExtendedAnomalyDetector",
    "run_extended_anomaly_detection",
    "detect_donor_conflicts",
    "detect_lobbying_overlaps",
    "detect_contract_front_runs",
    "run_tier2_detection",
    "transaction_amount",
    "persist_anomalies",
]
