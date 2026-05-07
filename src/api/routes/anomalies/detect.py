"""Anomaly analysis endpoints — run detectors and regenerate anomaly rows."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.analysis import (
    analyze_trades,
    analyze_wealth,
    run_advanced_anomaly_detection,
    run_extended_anomaly_detection,
)
from src.api.auth import require_admin
from src.db import Anomaly, get_db_session

router = APIRouter()


@router.post("/analyze")
async def run_analysis(
    member_id: int | None = Query(None, description="Analyze specific member"),
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Run anomaly analysis on members.

    Runs the full detector suite: wealth, trades, advanced (wealth-vs-salary,
    rapid asset appreciation, stock outperformance), and extended (trade
    timing, committee conflicts, loss avoidance, multi-factor risk).
    """
    wealth_result = analyze_wealth(db, member_id)
    trade_result = analyze_trades(db, member_id)

    # Advanced + extended detectors don't currently support per-member
    # filtering — only run them when no member filter is set.
    advanced_result: Dict[str, Any] = {}
    extended_result: Dict[str, Any] = {}
    if member_id is None:
        advanced_result = run_advanced_anomaly_detection(db)
        extended_result = run_extended_anomaly_detection(db, advanced_result)

    return {
        "status": "success",
        "message": "Analysis complete",
        "wealth": wealth_result,
        "trades": trade_result,
        "advanced": advanced_result,
        "extended": extended_result,
    }


@router.post("/regenerate")
async def regenerate_anomalies(
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Clear all anomalies and regenerate them with the full detector suite."""
    from src.analysis.trade_analyzer import TradeAnalyzer
    from src.analysis.wealth_analyzer import WealthAnalyzer

    deleted_count = db.query(Anomaly).delete()
    db.commit()

    trade_result = TradeAnalyzer().analyze_all_members(db)
    wealth_result = WealthAnalyzer().analyze_all_members(db)
    advanced_result = run_advanced_anomaly_detection(db)
    extended_result = run_extended_anomaly_detection(db, advanced_result)

    return {
        "status": "success",
        "message": "Anomalies regenerated",
        "deleted": deleted_count,
        "trade_anomalies": trade_result.get("total_anomalies", 0),
        "wealth_anomalies": wealth_result.get("total_anomalies", 0),
        "advanced_anomalies": advanced_result.get("total", 0),
        "extended_anomalies": extended_result.get("total", 0),
    }
