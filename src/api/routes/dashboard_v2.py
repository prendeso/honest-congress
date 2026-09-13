"""Dashboard pages.

The HTML for each page lives in `src/templates/<page>.html`. These route
handlers do nothing but render the template; data is fetched client-side
from `/api/*` JSON endpoints.

This file used to be ~1,800 lines of HTML embedded in Python f-strings.
The HTML was extracted by `scripts/migrate_dashboard_to_templates.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.templating import templates
from src.db import Anomaly, Disclosure, Member, Transaction, get_db_session

router = APIRouter()


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _render(request: Request, template: str) -> HTMLResponse:
    """Helper to render a template with the no-cache headers we want for
    the dashboard pages (browser back-button shouldn't show stale data)."""
    response = templates.TemplateResponse(request, template)
    for k, v in _NO_CACHE_HEADERS.items():
        response.headers[k] = v
    return response


# ---------------- pages ----------------


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> HTMLResponse:
    return _render(request, "home.html")


@router.get("/members", response_class=HTMLResponse)
async def members_page(request: Request) -> HTMLResponse:
    return _render(request, "members.html")


@router.get("/disclosures", response_class=HTMLResponse)
async def disclosures_page(request: Request) -> HTMLResponse:
    return _render(request, "disclosures.html")


@router.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request) -> HTMLResponse:
    return _render(request, "trades.html")


@router.get("/parsed", response_class=HTMLResponse)
async def parsed_page(request: Request) -> HTMLResponse:
    return _render(request, "parsed.html")


@router.get("/anomalies", response_class=HTMLResponse)
async def anomalies_page(request: Request) -> HTMLResponse:
    return _render(request, "anomalies.html")


@router.get("/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request) -> HTMLResponse:
    """Filing lateness and disclosure legibility.

    Both were reachable only through the API and the CLI. They are the two
    least interpretive things the project computes -- one is the subtraction of
    two dates that both appear on the filing, the other counts what could not
    be read -- so they are also the two least arguable, and the site had no
    page for either.
    """
    return _render(request, "compliance.html")


# ---------------- insights JSON ----------------


@router.get("/api/insights", tags=["Insights"])
async def get_insights(db: Session = Depends(get_db_session)) -> list[dict[str, Any]]:
    """Get interesting facts and insights from congressional data."""
    insights: list[dict[str, Any]] = []

    try:
        top_anomalies_members = (
            db.query(
                Member.first_name,
                Member.last_name,
                func.count(Anomaly.id).label("anomaly_count"),
            )
            .join(Anomaly, Member.id == Anomaly.member_id)
            .group_by(Member.id, Member.first_name, Member.last_name)
            .order_by(func.count(Anomaly.id).desc())
            .limit(1)
            .first()
        )

        if top_anomalies_members:
            member_name = f"{top_anomalies_members[0]} {top_anomalies_members[1]}"
            insights.append(
                {
                    "id": 1,
                    "icon": "🚨",
                    "title": "Most Flagged Member",
                    "description": "Member with the highest number of detected anomalies",
                    "value": f"{member_name} ({top_anomalies_members[2]} flags)",
                }
            )

        largest_trade = (
            db.query(
                Transaction.description,
                Transaction.amount_max,
                Member.first_name,
                Member.last_name,
            )
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .join(Member, Disclosure.member_id == Member.id)
            .order_by(Transaction.amount_max.desc())
            .first()
        )

        if largest_trade and largest_trade[1]:
            amount = int(largest_trade[1])
            insights.append(
                {
                    "id": 2,
                    "icon": "📈",
                    "title": "Largest Single Trade",
                    "description": "Highest value stock trade on record",
                    "value": f"${amount:,}",
                }
            )

        most_active = (
            db.query(
                Member.first_name,
                Member.last_name,
                func.count(Transaction.id).label("trade_count"),
            )
            .join(Disclosure, Member.id == Disclosure.member_id)
            .join(Transaction, Disclosure.id == Transaction.disclosure_id)
            .group_by(Member.id, Member.first_name, Member.last_name)
            .order_by(func.count(Transaction.id).desc())
            .limit(1)
            .first()
        )

        if most_active:
            member_name = f"{most_active[0]} {most_active[1]}"
            insights.append(
                {
                    "id": 3,
                    "icon": "📊",
                    "title": "Most Active Trader",
                    "description": "Member with highest frequency of stock trades",
                    "value": f"{member_name} ({most_active[2]} trades)",
                }
            )

        disclosure_count = db.query(func.count(Disclosure.id)).scalar()
        if disclosure_count:
            insights.append(
                {
                    "id": 4,
                    "icon": "📄",
                    "title": "Total Disclosures Analyzed",
                    "description": "Financial and transaction disclosure reports processed",
                    "value": f"{disclosure_count:,} filings",
                }
            )

    except Exception as e:
        # Insights are decorative — never let a query error crash the page.
        import logging

        logging.getLogger(__name__).warning("Error generating insights: %s", e)

    return insights
