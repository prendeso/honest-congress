"""Member API endpoints."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db import Anomaly, Chamber, Disclosure, Member, Party, get_db_session

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
    district: str | None
    in_office: bool
    disclosure_count: int = 0
    anomaly_count: int = 0

    model_config = ConfigDict(from_attributes=True)


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
    chamber: str | None = Query(None, description="Filter by chamber: house, senate"),
    party: str | None = Query(None, description="Filter by party: D, R, I"),
    state: str | None = Query(None, description="Filter by state code"),
    district: str | None = Query(None, description="Filter by district number"),
    search: str | None = Query(None, description="Search by name"),
    has_anomalies: bool | None = Query(None, description="Filter to members with anomalies"),
    in_office: bool | None = Query(None, description="Filter by active (true) or retired (false)"),
    min_disclosures: int | None = Query(None, description="Minimum number of disclosures", ge=0),
    min_anomalies: int | None = Query(None, description="Minimum number of anomalies", ge=0),
    sort_by: str | None = Query(
        "name",
        description="Sort field: name, party, state, chamber, district, status, disclosures, anomalies",
    ),
    sort_order: str | None = Query("asc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50000),
    db: Session = Depends(get_db_session),
):
    """
    List congressional members with optional filtering and server-side sorting.
    """
    query = db.query(Member)

    # Apply filters
    if chamber:
        query = query.filter(Member.chamber == Chamber(chamber.lower()))

    if party:
        query = query.filter(Member.party == Party(party.upper()))

    if state:
        query = query.filter(Member.state == state.upper())

    if district:
        # Handle special case: '-' means no district (senators)
        if district == "-":
            query = query.filter((Member.district == None) | (Member.district == "-1"))
        else:
            query = query.filter(Member.district == district)

    if in_office is not None:
        query = query.filter(Member.in_office == in_office)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Member.first_name.ilike(search_term)) | (Member.last_name.ilike(search_term))
        )

    if has_anomalies:
        # Filter to only members who have at least one anomaly
        query = query.filter(exists().where(Anomaly.member_id == Member.id))

    # Apply min_disclosures filter using materialized column
    if min_disclosures is not None:
        query = query.filter(Member.disclosure_count >= min_disclosures)

    # Apply min_anomalies filter using materialized column
    if min_anomalies is not None:
        query = query.filter(Member.anomaly_count >= min_anomalies)

    # Get total count
    total = query.count()

    # Apply server-side sorting using materialized columns
    from sqlalchemy import asc, desc

    if sort_order == "desc":
        if sort_by == "name":
            query = query.order_by(desc(Member.first_name), desc(Member.last_name))
        elif sort_by == "party":
            query = query.order_by(
                desc(Member.party), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "state":
            query = query.order_by(
                desc(Member.state), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "chamber":
            query = query.order_by(
                desc(Member.chamber), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "district":
            query = query.order_by(
                desc(Member.district), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "status":
            query = query.order_by(
                desc(Member.in_office), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "anomalies":
            query = query.order_by(
                desc(Member.anomaly_count), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "disclosures":
            query = query.order_by(
                desc(Member.disclosure_count), asc(Member.first_name), asc(Member.last_name)
            )
        else:
            query = query.order_by(desc(Member.first_name), desc(Member.last_name))
    else:  # asc
        if sort_by == "name":
            query = query.order_by(asc(Member.first_name), asc(Member.last_name))
        elif sort_by == "party":
            query = query.order_by(asc(Member.party), asc(Member.first_name), asc(Member.last_name))
        elif sort_by == "state":
            query = query.order_by(asc(Member.state), asc(Member.first_name), asc(Member.last_name))
        elif sort_by == "chamber":
            query = query.order_by(
                asc(Member.chamber), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "district":
            query = query.order_by(
                asc(Member.district), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "status":
            query = query.order_by(
                asc(Member.in_office), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "anomalies":
            query = query.order_by(
                asc(Member.anomaly_count), asc(Member.first_name), asc(Member.last_name)
            )
        elif sort_by == "disclosures":
            query = query.order_by(
                asc(Member.disclosure_count), asc(Member.first_name), asc(Member.last_name)
            )
        else:
            query = query.order_by(asc(Member.first_name), asc(Member.last_name))

    # A unique final tiebreak. Without it the sort keys above have ties --
    # thousands of rows share a severity, a filing year, a disclosure count --
    # and LIMIT/OFFSET over a non-deterministic order is free to return a row
    # on two pages and another on none. Measured on the live anomaly list, a
    # STATIC table: 4,888 rows paginated to 4,881 distinct, 7 duplicated and
    # 7 never returned at all. Anything walking these pages -- the site, an
    # export, an audit -- silently loses rows.
    query = query.order_by(Member.id.asc())

    # Apply pagination
    members = query.offset((page - 1) * page_size).limit(page_size).all()

    # Build response using materialized counts
    member_responses = []
    for member in members:
        member_responses.append(
            MemberResponse(
                id=member.id,
                bioguide_id=member.bioguide_id,
                first_name=member.first_name,
                last_name=member.last_name,
                chamber=member.chamber.value,
                party=member.party.value,
                state=member.state,
                district=None
                if member.district == "-1" or member.district is None
                else member.district,
                in_office=member.in_office,
                disclosure_count=member.disclosure_count,
                anomaly_count=member.anomaly_count,
            )
        )

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

    # Get recent disclosures
    recent_disclosures = (
        db.query(Disclosure)
        .filter(Disclosure.member_id == member.id)
        .order_by(Disclosure.filing_date.desc())
        .limit(5)
        .all()
    )

    # Get recent anomalies, under the same false-discovery filter the anomaly
    # list uses. This is the page that names one individual, so it is the last
    # place that should show a coincidence the correction rejected.
    #
    # `q_value IS NULL OR q_value <= alpha`, not a bare threshold: most
    # detectors measure a magnitude and have no null model, and dropping them
    # here would empty the page. NULL means untested, never failed.
    alpha = get_settings().fdr_alpha
    recent_anomalies = (
        db.query(Anomaly)
        .filter(Anomaly.member_id == member.id)
        .filter(or_(Anomaly.q_value.is_(None), Anomaly.q_value <= alpha))
        .order_by(Anomaly.detected_at.desc())
        .limit(5)
        .all()
    )

    return MemberDetailResponse(
        id=member.id,
        bioguide_id=member.bioguide_id,
        first_name=member.first_name,
        last_name=member.last_name,
        chamber=member.chamber.value,
        party=member.party.value,
        state=member.state,
        district=None if member.district == "-1" or member.district is None else member.district,
        in_office=member.in_office,
        disclosure_count=member.disclosure_count,
        anomaly_count=member.anomaly_count,
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
