"""Disclosure API endpoints."""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.db import Asset, Disclosure, Liability, Member, Transaction, get_db_session

router = APIRouter()


def _normalized_document_url(disclosure: Disclosure) -> str | None:
    url = disclosure.document_url

    if not disclosure.document_id:
        return url

    base = "https://disclosures-clerk.house.gov/public_disc"

    if disclosure.is_ptr:
        if url and "/ptr-pdfs/" in url:
            return url
        if url and "/financial-pdfs/" in url:
            return url.replace("/financial-pdfs/", "/ptr-pdfs/")
        if url and "disclosures-clerk.house.gov" in url and url.endswith(".pdf"):
            return url
        return f"{base}/ptr-pdfs/{disclosure.filing_year}/{disclosure.document_id}.pdf"

    if url and "/financial-pdfs/" in url:
        return url
    if url and "/ptr-pdfs/" in url:
        return url.replace("/ptr-pdfs/", "/financial-pdfs/")
    if url and "disclosures-clerk.house.gov" in url and url.endswith(".pdf"):
        return url
    return f"{base}/financial-pdfs/{disclosure.filing_year}/{disclosure.document_id}.pdf"


class AssetResponse(BaseModel):
    """Asset response schema."""

    id: int
    asset_type: str
    description: str
    ticker: str | None
    value_min: float | None
    value_max: float | None
    income_min: float | None
    income_max: float | None

    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(BaseModel):
    """Transaction response schema."""

    id: int
    transaction_type: str
    description: str
    ticker: str | None
    transaction_date: datetime | None
    amount_min: float | None
    amount_max: float | None
    owner: str | None

    model_config = ConfigDict(from_attributes=True)


class LiabilityResponse(BaseModel):
    """Liability response schema."""

    id: int
    creditor: str
    description: str | None
    amount_min: float | None
    amount_max: float | None

    model_config = ConfigDict(from_attributes=True)


class DisclosureResponse(BaseModel):
    """Disclosure response schema."""

    id: int
    member_id: int
    member_name: str
    filing_year: int
    filing_type: str
    filing_date: datetime | None
    document_id: str
    document_url: str | None
    parsed: bool
    is_ptr: bool = False
    # How much of the document the parser read, 0-1. `parsed` only ever meant
    # the parser ran without raising; this is what says whether it worked.
    # Null means never scored, not scored and fine.
    parse_confidence: float | None = None
    parse_warnings: str | None = None
    # Whether the PDF had any text in it. A false here means the score of 0.0
    # is the document's doing, not the parser's -- about one House PTR in eight
    # is a scan of a paper form. Null means nobody has checked.
    has_text_layer: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class DisclosureDetailResponse(DisclosureResponse):
    """Detailed disclosure response with assets, transactions, liabilities."""

    total_assets_min: float | None = None
    total_assets_max: float | None = None
    assets: List[AssetResponse] = []
    transactions: List[TransactionResponse] = []
    liabilities: List[LiabilityResponse] = []


class DisclosureListResponse(BaseModel):
    """Paginated list of disclosures."""

    total: int
    page: int
    page_size: int
    disclosures: List[DisclosureResponse]


@router.get("", response_model=DisclosureListResponse)
async def list_disclosures(
    member_id: int | None = Query(None, description="Filter by member ID"),
    filing_year: int | None = Query(None, description="Filter by filing year"),
    filing_type: str | None = Query(None, description="Filter by filing type"),
    parsed: bool | None = Query(None, description="Filter by parsed status"),
    is_ptr: bool | None = Query(None, description="Filter by PTR (stock trade) status"),
    sort_by: str | None = Query(
        "filing_date", description="Field to sort by: member_name, year, filing_date, status"
    ),
    sort_order: str | None = Query("desc", description="Sort order: asc or desc"),
    max_confidence: float | None = Query(
        None,
        ge=0,
        le=1,
        description=(
            "Only filings the parser read no better than this, 0-1. The useful "
            "query is the low end: `parsed` says the parser ran, not that it "
            "worked, so this is how you find the filings whose data is thin. "
            "Filings with no score have never been scored, and are excluded "
            "from this filter rather than assumed good."
        ),
    ),
    has_text_layer: bool | None = Query(
        None,
        description=(
            "Filter by whether the PDF had any extractable text. Pair it with "
            "`max_confidence`: `has_text_layer=true` gives the filings the "
            "parser genuinely did badly on, and `has_text_layer=false` gives "
            "the scanned paper forms, which score 0 because there is nothing "
            "in them to read. Roughly one House PTR in eight is the latter."
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """
    List financial disclosures with optional filtering and sorting.
    """
    query = db.query(Disclosure).join(Member)

    # Apply filters
    if member_id:
        query = query.filter(Disclosure.member_id == member_id)

    if filing_year:
        query = query.filter(Disclosure.filing_year == filing_year)

    if filing_type:
        query = query.filter(Disclosure.filing_type.ilike(f"%{filing_type}%"))

    if parsed is not None:
        query = query.filter(Disclosure.parsed == parsed)

    if is_ptr is not None:
        query = query.filter(Disclosure.is_ptr == is_ptr)

    if max_confidence is not None:
        # `isnot(None)` deliberately: a filing parsed before scoring existed has
        # no score, and treating that as 0 would bury the real low scorers in a
        # list of filings nobody has looked at yet.
        query = query.filter(
            Disclosure.parse_confidence.isnot(None),
            Disclosure.parse_confidence <= max_confidence,
        )

    if has_text_layer is not None:
        query = query.filter(Disclosure.has_text_layer.is_(has_text_layer))

    # Get total count
    total = query.count()

    # Apply server-side sorting
    is_desc = sort_order.lower() == "desc"
    if sort_by == "member_name":
        query = query.order_by(
            Member.first_name.desc() if is_desc else Member.first_name.asc(),
            Member.last_name.desc() if is_desc else Member.last_name.asc(),
        )
    elif sort_by == "year":
        query = query.order_by(
            Disclosure.filing_year.desc() if is_desc else Disclosure.filing_year.asc()
        )
    elif sort_by == "type":
        query = query.order_by(
            Disclosure.filing_type.desc() if is_desc else Disclosure.filing_type.asc()
        )
    elif sort_by == "status":
        query = query.order_by(Disclosure.parsed.desc() if is_desc else Disclosure.parsed.asc())
    else:  # Default to filing_date
        query = query.order_by(
            Disclosure.filing_date.desc() if is_desc else Disclosure.filing_date.asc()
        )

    # Apply pagination
    disclosures = query.offset((page - 1) * page_size).limit(page_size).all()

    return DisclosureListResponse(
        total=total,
        page=page,
        page_size=page_size,
        disclosures=[
            DisclosureResponse(
                id=d.id,
                member_id=d.member_id,
                member_name=f"{d.member.first_name} {d.member.last_name}",
                filing_year=d.filing_year,
                filing_type=d.filing_type,
                filing_date=d.filing_date,
                document_id=d.document_id,
                document_url=_normalized_document_url(d),
                parsed=d.parsed,
                is_ptr=d.is_ptr,
                parse_confidence=d.parse_confidence,
                parse_warnings=d.parse_warnings,
                has_text_layer=d.has_text_layer,
            )
            for d in disclosures
        ],
    )


@router.get("/{disclosure_id}", response_model=DisclosureDetailResponse)
async def get_disclosure(
    disclosure_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Get detailed information for a specific disclosure.
    """
    disclosure = db.query(Disclosure).filter(Disclosure.id == disclosure_id).first()

    if not disclosure:
        raise HTTPException(status_code=404, detail="Disclosure not found")

    # Get assets
    assets = db.query(Asset).filter(Asset.disclosure_id == disclosure_id).all()

    # Get transactions
    transactions = db.query(Transaction).filter(Transaction.disclosure_id == disclosure_id).all()

    # Get liabilities
    liabilities = db.query(Liability).filter(Liability.disclosure_id == disclosure_id).all()

    # Calculate totals
    total_min = sum(float(a.value_min) for a in assets if a.value_min) if assets else None
    total_max = sum(float(a.value_max) for a in assets if a.value_max) if assets else None

    return DisclosureDetailResponse(
        id=disclosure.id,
        member_id=disclosure.member_id,
        member_name=f"{disclosure.member.first_name} {disclosure.member.last_name}",
        filing_year=disclosure.filing_year,
        filing_type=disclosure.filing_type,
        filing_date=disclosure.filing_date,
        document_id=disclosure.document_id,
        document_url=_normalized_document_url(disclosure),
        parsed=disclosure.parsed,
        parse_confidence=disclosure.parse_confidence,
        parse_warnings=disclosure.parse_warnings,
        has_text_layer=disclosure.has_text_layer,
        total_assets_min=total_min,
        total_assets_max=total_max,
        assets=[
            AssetResponse(
                id=a.id,
                asset_type=a.asset_type.value,
                description=a.description,
                ticker=a.ticker,
                value_min=float(a.value_min) if a.value_min else None,
                value_max=float(a.value_max) if a.value_max else None,
                income_min=float(a.income_min) if a.income_min else None,
                income_max=float(a.income_max) if a.income_max else None,
            )
            for a in assets
        ],
        transactions=[
            TransactionResponse(
                id=t.id,
                transaction_type=t.transaction_type.value,
                description=t.description,
                ticker=t.ticker,
                transaction_date=t.transaction_date,
                amount_min=float(t.amount_min) if t.amount_min else None,
                amount_max=float(t.amount_max) if t.amount_max else None,
                owner=t.owner,
            )
            for t in transactions
        ],
        liabilities=[
            LiabilityResponse(
                id=lia.id,
                creditor=lia.creditor,
                description=lia.description,
                amount_min=float(lia.amount_min) if lia.amount_min else None,
                amount_max=float(lia.amount_max) if lia.amount_max else None,
            )
            for lia in liabilities
        ],
    )


@router.get("/member/{member_id}/summary")
async def get_member_disclosure_summary(
    member_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Get summary of all disclosures for a member with net worth over time.
    """
    member = db.query(Member).filter(Member.id == member_id).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    disclosures = (
        db.query(Disclosure)
        .filter(Disclosure.member_id == member_id, Disclosure.parsed == True)
        .order_by(Disclosure.filing_year)
        .all()
    )

    net_worth_history = []

    for disclosure in disclosures:
        assets = db.query(Asset).filter(Asset.disclosure_id == disclosure.id).all()
        liabilities = db.query(Liability).filter(Liability.disclosure_id == disclosure.id).all()

        total_assets_min = sum(float(a.value_min or 0) for a in assets)
        total_assets_max = sum(float(a.value_max or 0) for a in assets)
        total_liabilities_min = sum(float(lia.amount_min or 0) for lia in liabilities)
        total_liabilities_max = sum(float(lia.amount_max or 0) for lia in liabilities)

        net_worth_history.append(
            {
                "year": disclosure.filing_year,
                "disclosure_id": disclosure.id,
                "assets_min": total_assets_min,
                "assets_max": total_assets_max,
                "liabilities_min": total_liabilities_min,
                "liabilities_max": total_liabilities_max,
                "net_worth_min": total_assets_min - total_liabilities_max,
                "net_worth_max": total_assets_max - total_liabilities_min,
            }
        )

    return {
        "member_id": member_id,
        "member_name": f"{member.first_name} {member.last_name}",
        "total_disclosures": len(disclosures),
        "net_worth_history": net_worth_history,
    }
