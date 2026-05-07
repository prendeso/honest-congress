"""Read-only anomaly endpoints: list, summary, get-by-id, mark-reviewed."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.routes.anomalies._shared import (
    AnomalyListResponse,
    AnomalyResponse,
    AnomalySummaryResponse,
    ensure_large_trade_sync,
)
from src.db import Anomaly, Disclosure, Member, get_db_session

router = APIRouter()


@router.get("/", response_model=AnomalyListResponse)
async def list_anomalies(
    member_id: int | None = Query(None, description="Filter by member ID"),
    anomaly_type: str | None = Query(None, description="Filter by anomaly type"),
    severity: str | None = Query(None, description="Filter by severity: low, medium, high"),
    reviewed: bool | None = Query(None, description="Filter by reviewed status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """List detected anomalies with optional filtering."""
    ensure_large_trade_sync(db)

    query = (
        db.query(Anomaly).join(Member).outerjoin(Disclosure, Anomaly.disclosure_id == Disclosure.id)
    )

    if member_id:
        query = query.filter(Anomaly.member_id == member_id)
    if anomaly_type:
        query = query.filter(Anomaly.anomaly_type == anomaly_type)
    if severity:
        query = query.filter(Anomaly.severity == severity.lower())
    if reviewed is not None:
        query = query.filter(Anomaly.reviewed == reviewed)

    total = query.count()

    severity_order = {"high": 1, "medium": 2, "low": 3}
    anomalies = (
        query.order_by(Anomaly.detected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # SQLite doesn't support CASE-based ordering cleanly, so sort in Python.
    anomalies = sorted(
        anomalies,
        key=lambda a: (severity_order.get(a.severity, 4), -a.detected_at.timestamp()),
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
                member_in_office=(a.member.in_office if a.member.in_office is not None else False),
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
                filing_year=(
                    db.query(Disclosure.filing_year)
                    .filter(Disclosure.id == a.disclosure_id)
                    .scalar()
                    if a.disclosure_id
                    else None
                ),
            )
            for a in anomalies
        ],
    )


@router.get("/summary", response_model=AnomalySummaryResponse)
async def get_anomaly_summary(db: Session = Depends(get_db_session)):
    """Get summary statistics of all anomalies."""
    ensure_large_trade_sync(db)

    total = db.query(Anomaly).count()

    by_type_raw = (
        db.query(Anomaly.anomaly_type, func.count(Anomaly.id)).group_by(Anomaly.anomaly_type).all()
    )
    by_type = dict(by_type_raw)

    by_severity_raw = (
        db.query(Anomaly.severity, func.count(Anomaly.id)).group_by(Anomaly.severity).all()
    )
    by_severity = dict(by_severity_raw)

    by_party_raw = (
        db.query(Member.party, func.count(Anomaly.id)).join(Anomaly).group_by(Member.party).all()
    )
    by_party = {p.value: c for p, c in by_party_raw}

    by_chamber_raw = (
        db.query(Member.chamber, func.count(Anomaly.id))
        .join(Anomaly)
        .group_by(Member.chamber)
        .all()
    )
    by_chamber = {ch.value: c for ch, c in by_chamber_raw}

    return AnomalySummaryResponse(
        total_anomalies=total,
        by_type=by_type,
        by_severity=by_severity,
        by_party=by_party,
        by_chamber=by_chamber,
    )


@router.get("/{anomaly_id}", response_model=AnomalyResponse)
async def get_anomaly(
    anomaly_id: int,
    db: Session = Depends(get_db_session),
):
    """Get detailed information for a specific anomaly."""
    ensure_large_trade_sync(db)

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
        member_in_office=(
            anomaly.member.in_office if anomaly.member.in_office is not None else False
        ),
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
        filing_year=(
            db.query(Disclosure.filing_year).filter(Disclosure.id == anomaly.disclosure_id).scalar()
            if anomaly.disclosure_id
            else None
        ),
    )


@router.post("/{anomaly_id}/review")
async def mark_anomaly_reviewed(
    anomaly_id: int,
    db: Session = Depends(get_db_session),
):
    """Mark an anomaly as reviewed."""
    anomaly = db.query(Anomaly).filter(Anomaly.id == anomaly_id).first()
    if not anomaly:
        raise HTTPException(status_code=404, detail="Anomaly not found")

    anomaly.reviewed = True
    db.commit()

    return {"status": "success", "message": "Anomaly marked as reviewed"}
