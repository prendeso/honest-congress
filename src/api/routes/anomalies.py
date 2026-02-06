"""Anomaly API endpoints."""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal
import threading
import time

from src.db import get_db_session, Anomaly, Member, Disclosure, Transaction
from src.analysis import analyze_wealth
from src.analysis.trade_analyzer import TradeAnalyzer
from src.config import get_settings
from src.api.auth import issue_admin_token, revoke_admin_token, require_admin

router = APIRouter()

# One-time per-process sync to keep large-trade anomalies consistent.
_large_trade_synced = False

# Global sync status for progress tracking
_sync_status: Dict[str, Any] = {
    "running": False,
    "operation": None,
    "progress": 0,
    "total": 0,
    "message": "",
    "started_at": None,
    "completed_at": None,
    "result": None,
}
_sync_lock = threading.Lock()


def _large_trade_sync_needed(db: Session) -> bool:
    large_trade_threshold = Decimal("1000000")
    large_txn_count = db.query(func.count(Transaction.id)).filter(
        Transaction.amount_min > large_trade_threshold
    ).scalar()
    anomaly_count = db.query(func.count(Anomaly.id)).filter(
        Anomaly.anomaly_type == "large_trade",
        Anomaly.transaction_id.isnot(None)
    ).scalar()
    missing_txn_id = db.query(Anomaly.id).filter(
        Anomaly.anomaly_type == "large_trade",
        Anomaly.transaction_id.is_(None)
    ).first()

    return bool(missing_txn_id) or large_txn_count != anomaly_count


def _ensure_large_trade_sync(db: Session) -> None:
    global _large_trade_synced
    if _large_trade_synced and not _large_trade_sync_needed(db):
        return
    TradeAnalyzer()._sync_large_trade_anomalies(db)
    _large_trade_synced = True


class AnomalyResponse(BaseModel):
    """Anomaly response schema."""
    id: int
    member_id: int
    member_name: str
    member_party: str
    member_state: str
    member_chamber: str
    member_in_office: bool
    anomaly_type: str
    severity: str
    title: str
    description: str
    computed_value: Optional[float]
    threshold_value: Optional[float]
    detected_at: datetime
    reviewed: bool
    disclosure_id: Optional[int]
    transaction_id: Optional[int]
    filing_year: Optional[int]

    class Config:
        from_attributes = True


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


@router.get("", response_model=AnomalyListResponse)
async def list_anomalies(
    member_id: Optional[int] = Query(None, description="Filter by member ID"),
    anomaly_type: Optional[str] = Query(None, description="Filter by anomaly type"),
    severity: Optional[str] = Query(None, description="Filter by severity: low, medium, high"),
    reviewed: Optional[bool] = Query(None, description="Filter by reviewed status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """
    List detected anomalies with optional filtering.
    """
    _ensure_large_trade_sync(db)

    query = db.query(Anomaly).join(Member).outerjoin(Disclosure, Anomaly.disclosure_id == Disclosure.id)

    # Apply filters
    if member_id:
        query = query.filter(Anomaly.member_id == member_id)

    if anomaly_type:
        query = query.filter(Anomaly.anomaly_type == anomaly_type)

    if severity:
        query = query.filter(Anomaly.severity == severity.lower())

    if reviewed is not None:
        query = query.filter(Anomaly.reviewed == reviewed)

    # Get total count
    total = query.count()

    # Apply pagination and ordering (high severity first)
    severity_order = {"high": 1, "medium": 2, "low": 3}
    anomalies = query.order_by(
        Anomaly.detected_at.desc()
    ).offset((page - 1) * page_size).limit(page_size).all()

    # Sort by severity in Python (SQLite doesn't support CASE easily)
    anomalies = sorted(
        anomalies,
        key=lambda a: (severity_order.get(a.severity, 4), -a.detected_at.timestamp())
    )

    return AnomalyListResponse(
        total=total,
        page=page,
        page_size=page_size,
        anomalies=[
            AnomalyResponse(
                id=a.id,
                member_id=a.member_id,
                member_name=f"{a.member.first_name} {a.member.last_name}",
                member_party=a.member.party.value,
                member_state=a.member.state,
                member_chamber=a.member.chamber.value if a.member.chamber else None,
                member_in_office=a.member.in_office if a.member.in_office is not None else False,
                anomaly_type=a.anomaly_type,
                severity=a.severity,
                title=a.title,
                description=a.description,
                computed_value=float(a.computed_value) if a.computed_value else None,
                threshold_value=float(a.threshold_value) if a.threshold_value else None,
                detected_at=a.detected_at,
                reviewed=a.reviewed,
                disclosure_id=a.disclosure_id,
                transaction_id=a.transaction_id,
                filing_year=db.query(Disclosure.filing_year).filter(Disclosure.id == a.disclosure_id).scalar() if a.disclosure_id else None,
            )
            for a in anomalies
        ],
    )


@router.get("/summary", response_model=AnomalySummaryResponse)
async def get_anomaly_summary(db: Session = Depends(get_db_session)):
    """
    Get summary statistics of all anomalies.
    """
    _ensure_large_trade_sync(db)

    # Total count
    total = db.query(Anomaly).count()

    # By type
    by_type_raw = db.query(
        Anomaly.anomaly_type, func.count(Anomaly.id)
    ).group_by(Anomaly.anomaly_type).all()
    by_type = {t: c for t, c in by_type_raw}

    # By severity
    by_severity_raw = db.query(
        Anomaly.severity, func.count(Anomaly.id)
    ).group_by(Anomaly.severity).all()
    by_severity = {s: c for s, c in by_severity_raw}

    # By party
    by_party_raw = db.query(
        Member.party, func.count(Anomaly.id)
    ).join(Anomaly).group_by(Member.party).all()
    by_party = {p.value: c for p, c in by_party_raw}

    # By chamber
    by_chamber_raw = db.query(
        Member.chamber, func.count(Anomaly.id)
    ).join(Anomaly).group_by(Member.chamber).all()
    by_chamber = {ch.value: c for ch, c in by_chamber_raw}

    return AnomalySummaryResponse(
        total_anomalies=total,
        by_type=by_type,
        by_severity=by_severity,
        by_party=by_party,
        by_chamber=by_chamber,
    )


# -----------------------------------------------------------------
# Admin routes MUST come BEFORE /{anomaly_id} to avoid matching
# -----------------------------------------------------------------

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
    """
    Remove anomalies where computed_value == threshold_value.
    These should not have been flagged - anomalies require EXCEEDING the threshold.
    """
    all_anomalies = db.query(Anomaly).filter(
        Anomaly.computed_value.isnot(None),
        Anomaly.threshold_value.isnot(None)
    ).all()

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
        "remaining_anomalies": remaining
    }


@router.post("/analyze")
async def run_analysis(
    member_id: Optional[int] = Query(None, description="Analyze specific member"),
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """
    Run anomaly analysis on members.
    """
    result = analyze_wealth(db, member_id)

    return {
        "status": "success",
        "message": "Analysis complete",
        "result": result,
    }


@router.post("/regenerate")
async def regenerate_anomalies(
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """
    Clear all anomalies and regenerate them with fresh analysis.
    """
    from src.analysis.trade_analyzer import TradeAnalyzer
    from src.analysis.wealth_analyzer import WealthAnalyzer

    deleted_count = db.query(Anomaly).delete()
    db.commit()

    trade_analyzer = TradeAnalyzer()
    trade_result = trade_analyzer.analyze_all_members(db)

    wealth_analyzer = WealthAnalyzer()
    wealth_result = wealth_analyzer.analyze_all_members(db)

    return {
        "status": "success",
        "message": "Anomalies regenerated",
        "deleted": deleted_count,
        "trade_anomalies": trade_result.get("total_anomalies", 0),
        "wealth_anomalies": wealth_result.get("total_anomalies", 0),
    }


@router.get("/sync-status")
async def get_sync_status():
    """
    Get current sync operation status for progress indicator.
    """
    with _sync_lock:
        return {**_sync_status}


@router.post("/sync-all-members")
async def sync_all_members(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    _: str = Depends(require_admin),
):
    """
    Sync ALL members (current + historical, active + retired) from unitedstates.io.
    Runs in background with progress tracking.
    """
    global _sync_status

    with _sync_lock:
        if _sync_status["running"]:
            raise HTTPException(status_code=409, detail="Another sync operation is already running")

        _sync_status = {
            "running": True,
            "operation": "sync-all-members",
            "progress": 0,
            "total": 100,
            "message": "Starting member sync...",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "result": None,
        }

    def run_sync():
        global _sync_status
        try:
            from src.ingestion.orchestrator import IngestionOrchestrator
            from src.db.database import SessionLocal

            with _sync_lock:
                _sync_status["message"] = "Fetching members from unitedstates.io..."
                _sync_status["progress"] = 10

            sync_db = SessionLocal()
            try:
                orchestrator = IngestionOrchestrator()

                with _sync_lock:
                    _sync_status["message"] = "Processing members..."
                    _sync_status["progress"] = 30

                result = orchestrator.sync_all_members(sync_db)

                with _sync_lock:
                    _sync_status["progress"] = 100
                    _sync_status["message"] = f"Synced {result['total']} members"
                    _sync_status["result"] = result
                    _sync_status["running"] = False
                    _sync_status["completed_at"] = datetime.now().isoformat()
            finally:
                sync_db.close()

        except Exception as e:
            with _sync_lock:
                _sync_status["running"] = False
                _sync_status["message"] = f"Error: {str(e)}"
                _sync_status["completed_at"] = datetime.now().isoformat()

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
    """
    Sync all trades from QuiverQuant API.
    Runs in background with progress tracking.
    """
    global _sync_status

    with _sync_lock:
        if _sync_status["running"]:
            raise HTTPException(status_code=409, detail="Another sync operation is already running")

        _sync_status = {
            "running": True,
            "operation": "sync-trades",
            "progress": 0,
            "total": 100,
            "message": "Starting trade sync...",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "result": None,
        }

    def run_sync():
        global _sync_status
        try:
            from src.ingestion.quiverquant import ingest_quiverquant_trades
            from src.db.database import SessionLocal

            with _sync_lock:
                _sync_status["message"] = "Fetching trades from QuiverQuant..."
                _sync_status["progress"] = 20

            sync_db = SessionLocal()
            try:
                result = ingest_quiverquant_trades(sync_db, chamber="both")

                with _sync_lock:
                    _sync_status["progress"] = 100
                    _sync_status["message"] = f"Imported {result.get('imported', 0)} trades"
                    _sync_status["result"] = result
                    _sync_status["running"] = False
                    _sync_status["completed_at"] = datetime.now().isoformat()
            finally:
                sync_db.close()

        except Exception as e:
            with _sync_lock:
                _sync_status["running"] = False
                _sync_status["message"] = f"Error: {str(e)}"
                _sync_status["completed_at"] = datetime.now().isoformat()

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
    """
    Full data refresh: sync all members → sync all trades → regenerate anomalies.
    Runs in background with progress tracking.
    """
    global _sync_status

    with _sync_lock:
        if _sync_status["running"]:
            raise HTTPException(status_code=409, detail="Another sync operation is already running")

        _sync_status = {
            "running": True,
            "operation": "full-refresh",
            "progress": 0,
            "total": 100,
            "message": "Starting full data refresh...",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "result": None,
        }

    def run_full_refresh():
        global _sync_status
        try:
            from src.ingestion.orchestrator import IngestionOrchestrator
            from src.ingestion.quiverquant import ingest_quiverquant_trades
            from src.analysis.trade_analyzer import TradeAnalyzer
            from src.analysis.wealth_analyzer import WealthAnalyzer
            from src.db.database import SessionLocal

            sync_db = SessionLocal()
            results = {"members": {}, "trades": {}, "anomalies": {}}

            try:
                # Step 1: Sync all members (0-30%)
                with _sync_lock:
                    _sync_status["message"] = "Step 1/3: Syncing all members..."
                    _sync_status["progress"] = 5

                orchestrator = IngestionOrchestrator()
                results["members"] = orchestrator.sync_all_members(sync_db)

                with _sync_lock:
                    _sync_status["message"] = f"Step 1/3: Synced {results['members']['total']} members"
                    _sync_status["progress"] = 30

                # Step 2: Sync trades (30-60%)
                with _sync_lock:
                    _sync_status["message"] = "Step 2/3: Syncing trades from QuiverQuant..."
                    _sync_status["progress"] = 35

                results["trades"] = ingest_quiverquant_trades(sync_db, chamber="both")

                with _sync_lock:
                    _sync_status["message"] = f"Step 2/3: Imported {results['trades'].get('imported', 0)} trades"
                    _sync_status["progress"] = 60

                # Step 3: Regenerate anomalies (60-100%)
                with _sync_lock:
                    _sync_status["message"] = "Step 3/3: Regenerating anomalies..."
                    _sync_status["progress"] = 65

                # Clear existing anomalies
                deleted_count = sync_db.query(Anomaly).delete()
                sync_db.commit()

                with _sync_lock:
                    _sync_status["message"] = "Step 3/3: Running trade analysis..."
                    _sync_status["progress"] = 75

                trade_analyzer = TradeAnalyzer()
                trade_result = trade_analyzer.analyze_all_members(sync_db)

                with _sync_lock:
                    _sync_status["message"] = "Step 3/3: Running wealth analysis..."
                    _sync_status["progress"] = 90

                wealth_analyzer = WealthAnalyzer()
                wealth_result = wealth_analyzer.analyze_all_members(sync_db)

                results["anomalies"] = {
                    "deleted": deleted_count,
                    "trade_anomalies": trade_result.get("total_anomalies", 0),
                    "wealth_anomalies": wealth_result.get("total_anomalies", 0),
                }

                with _sync_lock:
                    _sync_status["progress"] = 100
                    _sync_status["message"] = "Full refresh complete!"
                    _sync_status["result"] = results
                    _sync_status["running"] = False
                    _sync_status["completed_at"] = datetime.now().isoformat()

            finally:
                sync_db.close()

        except Exception as e:
            with _sync_lock:
                _sync_status["running"] = False
                _sync_status["message"] = f"Error: {str(e)}"
                _sync_status["completed_at"] = datetime.now().isoformat()

    background_tasks.add_task(run_full_refresh)

    return {
        "status": "started",
        "message": "Full data refresh started in background. Check /sync-status for progress.",
    }


# -----------------------------------------------------------------
# Dynamic path routes AFTER static ones
# -----------------------------------------------------------------

@router.get("/{anomaly_id}", response_model=AnomalyResponse)
async def get_anomaly(
    anomaly_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Get detailed information for a specific anomaly.
    """
    _ensure_large_trade_sync(db)

    anomaly = db.query(Anomaly).filter(Anomaly.id == anomaly_id).first()

    if not anomaly:
        raise HTTPException(status_code=404, detail="Anomaly not found")

    return AnomalyResponse(
        id=anomaly.id,
        member_id=anomaly.member_id,
        member_name=f"{anomaly.member.first_name} {anomaly.member.last_name}",
        member_party=anomaly.member.party.value,
        member_state=anomaly.member.state,
        member_chamber=anomaly.member.chamber.value if anomaly.member.chamber else None,
        member_in_office=anomaly.member.in_office if anomaly.member.in_office is not None else False,
        anomaly_type=anomaly.anomaly_type,
        severity=anomaly.severity,
        title=anomaly.title,
        description=anomaly.description,
        computed_value=float(anomaly.computed_value) if anomaly.computed_value else None,
        threshold_value=float(anomaly.threshold_value) if anomaly.threshold_value else None,
        detected_at=anomaly.detected_at,
        reviewed=anomaly.reviewed,
        disclosure_id=anomaly.disclosure_id,
        transaction_id=anomaly.transaction_id,
        filing_year=db.query(Disclosure.filing_year).filter(Disclosure.id == anomaly.disclosure_id).scalar() if anomaly.disclosure_id else None,
    )


@router.post("/{anomaly_id}/review")
async def mark_anomaly_reviewed(
    anomaly_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Mark an anomaly as reviewed.
    """
    anomaly = db.query(Anomaly).filter(Anomaly.id == anomaly_id).first()

    if not anomaly:
        raise HTTPException(status_code=404, detail="Anomaly not found")

    anomaly.reviewed = True
    db.commit()

    return {"status": "success", "message": "Anomaly marked as reviewed"}
