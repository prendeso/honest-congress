"""STOCK Act filing compliance endpoints.

The least interpretive output the project produces: date subtraction over
public filings, with no claim about intent, profit or conflict.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.analysis.compliance import compliance_leaderboard, member_compliance
from src.analysis.opacity import member_opacity, opacity_leaderboard
from src.db import Member, get_db_session

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


@router.get("/")
async def get_compliance_leaderboard(
    min_transactions: int = Query(
        5,
        ge=1,
        description=(
            "Minimum checkable transactions for a member to be ranked. Guards "
            "against one late filing out of one transaction topping the list."
        ),
    ),
    limit: int | None = Query(None, ge=1, le=1000, description="Cap the number of members"),
    db: Session = Depends(get_db_session),
):
    """Members ranked by the share of PTRs filed after the statutory deadline."""
    return compliance_leaderboard(db, min_transactions=min_transactions, limit=limit)


@router.get("/opacity/")
async def get_opacity_leaderboard(
    limit: int | None = Query(None, ge=1, le=1000, description="Cap the number of members"),
    db: Session = Depends(get_db_session),
):
    """Members ranked from least to most legible disclosure.

    Measures how readable the filings are, not the conduct behind them.
    """
    return opacity_leaderboard(db, limit=limit)


@router.get("/opacity/{member_id}")
async def get_member_opacity(
    member_id: int,
    db: Session = Depends(get_db_session),
):
    """Disclosure legibility for a single member."""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    score = member_opacity(db, member)
    if score is None:
        raise HTTPException(
            status_code=404, detail="Not enough disclosed items to score this member"
        )
    return score


@router.get("/{member_id}")
async def get_member_compliance(
    member_id: int,
    db: Session = Depends(get_db_session),
):
    """Filing punctuality for a single member."""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    score = member_compliance(db, member)
    if score is None:
        raise HTTPException(
            status_code=404,
            detail="No checkable transactions for this member",
        )
    return score
