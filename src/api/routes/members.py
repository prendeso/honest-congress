"""Member API endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime

from src.db import get_db_session, Member, Disclosure, Anomaly, Chamber, Party

router = APIRouter()


class MemberResponse(BaseModel):
    """Member response schema."""
    id: int
    bioguide_id: str
    first_name: str
    last_name: str
    chamber: str
    party: str
    state: str
    district: Optional[str]
    in_office: bool
    disclosure_count: int = 0
    anomaly_count: int = 0

    class Config:
        from_attributes = True


class MemberDetailResponse(MemberResponse):
    """Detailed member response with recent disclosures."""
    recent_disclosures: List[dict] = []
    recent_anomalies: List[dict] = []


class MemberListResponse(BaseModel):
    """Paginated list of members."""
    total: int
    page: int
    page_size: int
    members: List[MemberResponse]


@router.get("", response_model=MemberListResponse)
async def list_members(
    chamber: Optional[str] = Query(None, description="Filter by chamber: house, senate"),
    party: Optional[str] = Query(None, description="Filter by party: D, R, I"),
    state: Optional[str] = Query(None, description="Filter by state code"),
    search: Optional[str] = Query(None, description="Search by name"),
    has_anomalies: Optional[bool] = Query(None, description="Filter to members with anomalies"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """
    List congressional members with optional filtering.
    """
    query = db.query(Member)

    # Apply filters
    if chamber:
        query = query.filter(Member.chamber == Chamber(chamber.lower()))

    if party:
        query = query.filter(Member.party == Party(party.upper()))

    if state:
        query = query.filter(Member.state == state.upper())

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Member.first_name.ilike(search_term)) |
            (Member.last_name.ilike(search_term))
        )

    if has_anomalies:
        # Subquery for members with anomalies
        anomaly_members = db.query(Anomaly.member_id).distinct()
        query = query.filter(Member.id.in_(anomaly_members))

    # Get total count
    total = query.count()

    # Apply pagination
    members = query.offset((page - 1) * page_size).limit(page_size).all()

    # Build response with counts
    member_responses = []
    for member in members:
        disclosure_count = db.query(Disclosure).filter(
            Disclosure.member_id == member.id
        ).count()

        anomaly_count = db.query(Anomaly).filter(
            Anomaly.member_id == member.id
        ).count()

        member_responses.append(MemberResponse(
            id=member.id,
            bioguide_id=member.bioguide_id,
            first_name=member.first_name,
            last_name=member.last_name,
            chamber=member.chamber.value,
            party=member.party.value,
            state=member.state,
            district=member.district,
            in_office=member.in_office,
            disclosure_count=disclosure_count,
            anomaly_count=anomaly_count,
        ))

    return MemberListResponse(
        total=total,
        page=page,
        page_size=page_size,
        members=member_responses,
    )


@router.get("/{member_id}", response_model=MemberDetailResponse)
async def get_member(
    member_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Get detailed information for a specific member.
    """
    member = db.query(Member).filter(Member.id == member_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get disclosure count
    disclosure_count = db.query(Disclosure).filter(
        Disclosure.member_id == member.id
    ).count()

    # Get anomaly count
    anomaly_count = db.query(Anomaly).filter(
        Anomaly.member_id == member.id
    ).count()

    # Get recent disclosures
    recent_disclosures = db.query(Disclosure).filter(
        Disclosure.member_id == member.id
    ).order_by(Disclosure.filing_date.desc()).limit(5).all()

    # Get recent anomalies
    recent_anomalies = db.query(Anomaly).filter(
        Anomaly.member_id == member.id
    ).order_by(Anomaly.detected_at.desc()).limit(5).all()

    return MemberDetailResponse(
        id=member.id,
        bioguide_id=member.bioguide_id,
        first_name=member.first_name,
        last_name=member.last_name,
        chamber=member.chamber.value,
        party=member.party.value,
        state=member.state,
        district=member.district,
        in_office=member.in_office,
        disclosure_count=disclosure_count,
        anomaly_count=anomaly_count,
        recent_disclosures=[
            {
                "id": d.id,
                "filing_year": d.filing_year,
                "filing_type": d.filing_type,
                "filing_date": d.filing_date.isoformat() if d.filing_date else None,
                "parsed": d.parsed,
            }
            for d in recent_disclosures
        ],
        recent_anomalies=[
            {
                "id": a.id,
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "title": a.title,
                "detected_at": a.detected_at.isoformat() if a.detected_at else None,
            }
            for a in recent_anomalies
        ],
    )


@router.get("/bioguide/{bioguide_id}", response_model=MemberDetailResponse)
async def get_member_by_bioguide(
    bioguide_id: str,
    db: Session = Depends(get_db_session),
):
    """
    Get member by Bioguide ID.
    """
    member = db.query(Member).filter(Member.bioguide_id == bioguide_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Reuse the get_member logic
    return await get_member(member.id, db)

