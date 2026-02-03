"""Assets API endpoints."""
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db import get_db_session
from src.db.models import Asset, Disclosure, Member

router = APIRouter(tags=["assets"])


class AssetResponse(BaseModel):
    """Asset response model."""
    id: int
    disclosure_id: int
    asset_type: str
    description: str
    ticker: Optional[str] = None
    value_min: Optional[float] = None
    value_max: Optional[float] = None
    income_min: Optional[float] = None
    income_max: Optional[float] = None
    member_name: Optional[str] = None


class AssetListResponse(BaseModel):
    """Asset list response."""
    assets: List[AssetResponse]
    total: int
    page: int
    page_size: int


@router.get("/assets", response_model=AssetListResponse)
async def list_assets(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    ticker: Optional[str] = Query(None, description="Filter by ticker symbol"),
    asset_type: Optional[str] = Query(None, description="Filter by asset type"),
    member_id: Optional[int] = Query(None, description="Filter by member ID"),
    db: Session = Depends(get_db_session),
):
    """List assets with pagination and filtering."""
    query = db.query(Asset).join(Disclosure)

    if ticker:
        query = query.filter(Asset.ticker.ilike(f"%{ticker}%"))

    if asset_type:
        query = query.filter(Asset.asset_type == asset_type)

    if member_id:
        query = query.filter(Disclosure.member_id == member_id)

    # Get total count
    total = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    assets = query.order_by(Asset.id.desc()).offset(offset).limit(page_size).all()

    # Build response with member names
    result = []
    for asset in assets:
        member_name = None
        if asset.disclosure and asset.disclosure.member:
            m = asset.disclosure.member
            member_name = f"{m.first_name} {m.last_name}"

        result.append(AssetResponse(
            id=asset.id,
            disclosure_id=asset.disclosure_id,
            asset_type=asset.asset_type.value if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
            description=asset.description,
            ticker=asset.ticker,
            value_min=float(asset.value_min) if asset.value_min else None,
            value_max=float(asset.value_max) if asset.value_max else None,
            income_min=float(asset.income_min) if asset.income_min else None,
            income_max=float(asset.income_max) if asset.income_max else None,
            member_name=member_name,
        ))

    return AssetListResponse(
        assets=result,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/assets/summary")
async def assets_summary(db: Session = Depends(get_db_session)):
    """Get summary statistics for assets."""
    # ...existing code...

@router.get("/assets/{asset_id}", response_model=AssetResponse)
async def get_asset(asset_id: int, db: Session = Depends(get_db_session)):
    """Get a specific asset by ID."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()

    if not asset:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Asset not found")

    member_name = None
    if asset.disclosure and asset.disclosure.member:
        m = asset.disclosure.member
        member_name = f"{m.first_name} {m.last_name}"

    return AssetResponse(
        id=asset.id,
        disclosure_id=asset.disclosure_id,
        asset_type=asset.asset_type.value if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
        description=asset.description,
        ticker=asset.ticker,
        value_min=float(asset.value_min) if asset.value_min else None,
        value_max=float(asset.value_max) if asset.value_max else None,
        income_min=float(asset.income_min) if asset.income_min else None,
        income_max=float(asset.income_max) if asset.income_max else None,
        member_name=member_name,
    )

