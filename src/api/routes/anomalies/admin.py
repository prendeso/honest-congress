"""Admin/sync endpoints — login/logout, cleanup, member sync, trade sync, full refresh."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.analysis import run_advanced_anomaly_detection, run_extended_anomaly_detection
from src.api.auth import issue_admin_token, require_admin, revoke_admin_token
from src.api.routes.anomalies._shared import sync_lock, sync_status
from src.config import get_settings
from src.db import Anomaly, get_db_session

router = APIRouter()


class AdminLoginRequest(BaseModel):
    password: str


@router.post("/admin/login")
async def admin_login(payload: AdminLoginRequest):
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin password not configured")
    if payload.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = issue_admin_token()
    return {"token": token}


@router.post("/admin/logout")
async def admin_logout(admin_token: str = Depends(require_admin)):
    revoke_admin_token(admin_token)
    return {"status": "success"}


@router.post("/cleanup")
async def cleanup_invalid_anomalies(
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Remove anomalies where computed_value == threshold_value.

    These should not have been flagged — anomalies require EXCEEDING the threshold.
    """
    all_anomalies = (
        db.query(Anomaly)
        .filter(Anomaly.computed_value.isnot(None), Anomaly.threshold_value.isnot(None))
        .all()
    )

    deleted_count = 0
    deleted_titles = []
    for a in all_anomalies:
        computed = float(a.computed_value) if a.computed_value else None
        threshold = float(a.threshold_value) if a.threshold_value else None
        if computed is not None and threshold is not None and computed == threshold:
            deleted_titles.append(a.title)
            db.delete(a)
            deleted_count += 1

    db.commit()
    remaining = db.query(Anomaly).count()

    return {
        "status": "success",
        "message": f"Deleted {deleted_count} invalid anomalies",
        "deleted_count": deleted_count,
        "deleted_titles": deleted_titles[:10],
        "remaining_anomalies": remaining,
    }


@router.get("/sync-status")
async def get_sync_status():
    """Get current sync operation status for progress indicator."""
    with sync_lock:
        return {**sync_status}


def _start_sync(operation: str, message: str) -> None:
    """Atomically claim the global sync slot or raise 409 if busy."""
    with sync_lock:
        if sync_status["running"]:
            raise HTTPException(status_code=409, detail="Another sync operation is already running")
        sync_status.update(
            {
                "running": True,
                "operation": operation,
                "progress": 0,
                "total": 100,
                "message": message,
                "started_at": datetime.now().isoformat(),
                "completed_at": None,
                "result": None,
            }
        )


def _finish_sync(success: bool, message: str, result=None) -> None:
    with sync_lock:
        sync_status["running"] = False
        sync_status["progress"] = 100 if success else sync_status["progress"]
        sync_status["message"] = message
        sync_status["result"] = result
        sync_status["completed_at"] = datetime.now().isoformat()


@router.post("/sync-all-members")
async def sync_all_members(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Sync ALL members (current + historical) from unitedstates.io."""
    _start_sync("sync-all-members", "Starting member sync...")

    def run_sync():
        try:
            from src.db.database import SessionLocal
            from src.ingestion.orchestrator import IngestionOrchestrator

            with sync_lock:
                sync_status["message"] = "Fetching members from unitedstates.io..."
                sync_status["progress"] = 10

            sync_db = SessionLocal()
            try:
                orchestrator = IngestionOrchestrator()
                with sync_lock:
                    sync_status["message"] = "Processing members..."
                    sync_status["progress"] = 30

                result = orchestrator.sync_all_members(sync_db)
                _finish_sync(True, f"Synced {result['total']} members", result)
            finally:
                sync_db.close()
        except Exception as e:
            _finish_sync(False, f"Error: {e}")

    background_tasks.add_task(run_sync)
    return {
        "status": "started",
        "message": "Member sync started in background. Check /sync-status for progress.",
    }


@router.post("/sync-trades")
async def sync_trades(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Sync all trades from QuiverQuant API."""
    _start_sync("sync-trades", "Starting trade sync...")

    def run_sync():
        try:
            from src.db.database import SessionLocal
            from src.ingestion.quiverquant import ingest_quiverquant_trades

            with sync_lock:
                sync_status["message"] = "Fetching trades from QuiverQuant..."
                sync_status["progress"] = 20

            sync_db = SessionLocal()
            try:
                result = ingest_quiverquant_trades(sync_db, chamber="both")
                _finish_sync(True, f"Imported {result.get('imported', 0)} trades", result)
            finally:
                sync_db.close()
        except Exception as e:
            _finish_sync(False, f"Error: {e}")

    background_tasks.add_task(run_sync)
    return {
        "status": "started",
        "message": "Trade sync started in background. Check /sync-status for progress.",
    }


@router.post("/full-refresh")
async def full_data_refresh(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """Full refresh: sync all members → sync all trades → regenerate anomalies."""
    _start_sync("full-refresh", "Starting full data refresh...")

    def run_full_refresh():
        try:
            from src.analysis.trade_analyzer import TradeAnalyzer
            from src.analysis.wealth_analyzer import WealthAnalyzer
            from src.db.database import SessionLocal
            from src.ingestion.orchestrator import IngestionOrchestrator
            from src.ingestion.quiverquant import ingest_quiverquant_trades

            sync_db = SessionLocal()
            results = {"members": {}, "trades": {}, "anomalies": {}}

            try:
                with sync_lock:
                    sync_status["message"] = "Step 1/3: Syncing all members..."
                    sync_status["progress"] = 5

                results["members"] = IngestionOrchestrator().sync_all_members(sync_db)

                with sync_lock:
                    sync_status["message"] = (
                        f"Step 1/3: Synced {results['members']['total']} members"
                    )
                    sync_status["progress"] = 30
                    sync_status["message"] = "Step 2/3: Syncing trades from QuiverQuant..."
                    sync_status["progress"] = 35

                results["trades"] = ingest_quiverquant_trades(sync_db, chamber="both")

                with sync_lock:
                    sync_status["message"] = (
                        f"Step 2/3: Imported {results['trades'].get('imported', 0)} trades"
                    )
                    sync_status["progress"] = 60
                    sync_status["message"] = "Step 3/3: Regenerating anomalies..."
                    sync_status["progress"] = 65

                deleted_count = sync_db.query(Anomaly).delete()
                sync_db.commit()

                with sync_lock:
                    sync_status["message"] = "Step 3/3: Running trade analysis..."
                    sync_status["progress"] = 75

                trade_result = TradeAnalyzer().analyze_all_members(sync_db)

                with sync_lock:
                    sync_status["message"] = "Step 3/3: Running wealth analysis..."
                    sync_status["progress"] = 90

                wealth_result = WealthAnalyzer().analyze_all_members(sync_db)

                with sync_lock:
                    sync_status["message"] = "Step 3/3: Running advanced + extended detection..."
                    sync_status["progress"] = 95

                advanced_result = run_advanced_anomaly_detection(sync_db)
                extended_result = run_extended_anomaly_detection(sync_db, advanced_result)

                results["anomalies"] = {
                    "deleted": deleted_count,
                    "trade_anomalies": trade_result.get("total_anomalies", 0),
                    "wealth_anomalies": wealth_result.get("total_anomalies", 0),
                    "advanced_anomalies": advanced_result.get("total", 0),
                    "extended_anomalies": extended_result.get("total", 0),
                }

                _finish_sync(True, "Full refresh complete!", results)
            finally:
                sync_db.close()
        except Exception as e:
            _finish_sync(False, f"Error: {e}")

    background_tasks.add_task(run_full_refresh)
    return {
        "status": "started",
        "message": "Full data refresh started in background. Check /sync-status for progress.",
    }
