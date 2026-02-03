"""Anomaly API endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal

from src.db import get_db_session, Anomaly, Member, Disclosure, Transaction
from src.analysis import analyze_wealth
from src.analysis.trade_analyzer import TradeAnalyzer
from src.config import get_settings
from src.api.auth import issue_admin_token, revoke_admin_token, require_admin

router = APIRouter()

# One-time per-process sync to keep large-trade anomalies consistent.
_large_trade_synced = False


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

