"""Schemas, sync state, and helpers shared across the anomalies sub-routers."""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.analysis.trade_analyzer import TradeAnalyzer
from src.db import Anomaly, Transaction

# ---------------- response schemas ----------------


class AnomalyResponse(BaseModel):
    """Anomaly response schema."""

    id: int
    member_id: int
    member_name: str
    member_party: str
    member_state: str
    # Both handlers emit None when a member has no chamber recorded, so the
    # schema has to allow it -- as declared, that raised a validation error.
    member_chamber: str | None
    member_in_office: bool
    anomaly_type: str
    severity: str
    title: str
    description: str
    computed_value: float | None
    threshold_value: float | None
    detected_at: datetime
    reviewed: bool
    disclosure_id: int | None
    transaction_id: int | None
    filing_year: int | None
    # Where this finding sits among others of its own type, 0-100.
    # Null when the population was too small to rank against.
    percentile_rank: float | None = None

    model_config = ConfigDict(from_attributes=True)


class AnomalyListResponse(BaseModel):
    """Paginated list of anomalies."""

    total: int
    page: int
    page_size: int
    anomalies: List[AnomalyResponse]


class AnomalySummaryResponse(BaseModel):
    """Summary of anomalies by type and severity."""

    total_anomalies: int
    by_type: dict
    by_severity: dict
    by_party: dict
    by_chamber: dict


# ---------------- large-trade sync state ----------------

# One-time per-process flag to avoid repeating the sync work on every request.
_large_trade_synced = False


def _large_trade_sync_needed(db: Session) -> bool:
    """Decide whether the large-trade anomaly cache is out of sync with rows."""
    large_trade_threshold = Decimal("1000000")
    large_txn_count = (
        db.query(func.count(Transaction.id))
        .filter(Transaction.amount_min > large_trade_threshold)
        .scalar()
    )
    anomaly_count = (
        db.query(func.count(Anomaly.id))
        .filter(
            Anomaly.anomaly_type == "large_trade",
            Anomaly.transaction_id.isnot(None),
        )
        .scalar()
    )
    missing_txn_id = (
        db.query(Anomaly.id)
        .filter(
            Anomaly.anomaly_type == "large_trade",
            Anomaly.transaction_id.is_(None),
        )
        .first()
    )

    return bool(missing_txn_id) or large_txn_count != anomaly_count


def ensure_large_trade_sync(db: Session) -> None:
    """Idempotent: bring the large-trade anomalies in line with transactions."""
    global _large_trade_synced
    if _large_trade_synced and not _large_trade_sync_needed(db):
        return
    TradeAnalyzer()._sync_large_trade_anomalies(db)
    _large_trade_synced = True


# ---------------- background sync status ----------------

# Shared progress tracker for the long-running /sync-* and /full-refresh
# endpoints. Pages can poll /api/anomalies/sync-status for updates.
sync_status: Dict[str, Any] = {
    "running": False,
    "operation": None,
    "progress": 0,
    "total": 0,
    "message": "",
    "started_at": None,
    "completed_at": None,
    "result": None,
}
sync_lock = threading.Lock()
