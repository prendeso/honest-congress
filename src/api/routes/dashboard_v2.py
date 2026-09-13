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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.analysis.compliance import late_filing_rate
from src.api.templating import templates
from src.db import Anomaly, Disclosure, Transaction, get_db_session

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
    """Headline figures for the landing page.

    Rewritten because the landing page is the first thing a visitor sees and
    two of its four cards said things this project does not support.

    "Most Flagged Member" named one real person, in the hero, as the member
    with the most anomalies. A flag count is not a fact about a person: it is
    dominated by trading volume, because a member who files a lot of trades
    trips `large_trade`, `volume_spikes`, `trade_clustering` and
    `high_trading_frequency` over and over. Presenting that as a ranking of
    members, above the fold, with no q-value and no caveat, is the exact thing
    the rest of the codebase spends its time refusing to do. It is gone.

    "Largest Single Trade: $5,000,000" read `amount_max` -- the UPPER BOUND of
    a disclosed band -- and printed it as a figure. The filing said
    $1,000,001-$5,000,000 and the page said five million. Bands are reported as
    bands here now, which is the oldest rule in this project.

    What is left is arithmetic over public filings: how much was read, how much
    was filed late, how many findings survive correction, and the largest
    disclosed band.
    """
    insights: list[dict[str, Any]] = []

    try:
        filings = db.query(func.count(Disclosure.id)).scalar() or 0
        if filings:
            # Three different things, and lumping them together made the page
            # claim something untrue. A filing nobody has parsed YET is not a
            # filing that could not be read: during a chunked rebuild the whole
            # remaining queue counted as unreadable and was described to the
            # public as scans of paper forms. Only the middle count below is
            # actually a scan -- no text layer at all -- and only the first two
            # are "could not be read", because the parser has been near them.
            unread = (
                db.query(func.count(Disclosure.id)).filter(Disclosure.parsed.is_(False)).scalar()
                or 0
            )
            scans = (
                db.query(func.count(Disclosure.id))
                .filter(
                    Disclosure.parsed.is_(True),
                    Disclosure.has_text_layer.is_(False),
                )
                .scalar()
                or 0
            )
            # Read, has text, and still yielded nothing: a limit of this
            # parser, not a property of the document. Saying so is the point.
            yielded_nothing = (
                db.query(func.count(Disclosure.id))
                .filter(
                    Disclosure.parsed.is_(True),
                    or_(
                        Disclosure.has_text_layer.is_(True),
                        Disclosure.has_text_layer.is_(None),
                    ),
                    Disclosure.parse_confidence == 0.0,
                )
                .scalar()
                or 0
            )

            clauses = []
            if scans:
                clauses.append(
                    f"{scans:,} {'is a scan' if scans == 1 else 'are scans'} of paper "
                    "forms, which hold no machine-readable text"
                )
            if yielded_nothing:
                clauses.append(
                    f"{yielded_nothing:,} {'was' if yielded_nothing == 1 else 'were'} "
                    "read but yielded nothing"
                )

            if clauses:
                description = (
                    f"Of these, {' and '.join(clauses)}. Nothing in those filings "
                    "appears anywhere on this site."
                )
            else:
                description = "Every filing counted here was read."

            if unread:
                description += (
                    f" A further {unread:,} {'filing has' if unread == 1 else 'filings have'} "
                    "not been read yet — queued, not unreadable."
                )

            insights.append(
                {
                    "id": 1,
                    "icon": "📄",
                    "title": "Filings analysed",
                    "description": description,
                    "value": f"{filings:,} filings",
                }
            )

        # The most defensible number here: two dates that both appear on the
        # filing, subtracted. No threshold anybody chose.
        # Two aggregates, not a full leaderboard. Building the leaderboard here
        # meant the landing page scored every filer individually on each load.
        late = late_filing_rate(db)
        if late["transactions_checked"]:
            insights.append(
                {
                    "id": 2,
                    "icon": "⏱️",
                    "title": "Reported after the deadline",
                    "description": (
                        f"Share of {late['transactions_checked']:,} disclosed trades "
                        f"filed more than {late['deadline_days']} days after the trade, "
                        "which is what the STOCK Act allows."
                    ),
                    "value": f"{late['late_rate_percent']}%",
                }
            )

        largest = (
            db.query(Transaction.amount_min, Transaction.amount_max)
            .filter(Transaction.amount_max.isnot(None))
            .order_by(Transaction.amount_max.desc())
            .first()
        )
        if largest:
            low, high = largest
            # A band, said as a band. The filing does not contain a figure.
            band = f"${int(high):,}" if low is None else f"${int(low):,}–${int(high):,}"
            insights.append(
                {
                    "id": 3,
                    "icon": "📈",
                    "title": "Largest disclosed trade",
                    "description": (
                        "STOCK Act filings report a range, never an amount, and never a "
                        "share count. This is the widest band anyone disclosed, not a sum "
                        "anyone was paid."
                    ),
                    "value": band,
                }
            )

        findings = db.query(func.count(Anomaly.id)).scalar() or 0
        if findings:
            # This counted `q_value IS NOT NULL`, which means WAS TESTED, and
            # published it under the word "survive". Those are different claims
            # and the gap is not academic: the live site read "1" when exactly
            # zero findings passed FDR and one had merely been testable. A
            # q-value of 0.97 -- tested, comprehensively failed -- was counted as
            # surviving correction.
            #
            # `significance_summary` already draws the distinction the rest of
            # this codebase insists on, so use it rather than a fourth spelling
            # of the same query.
            from src.analysis.baselines import significance_summary

            summary = significance_summary(db)
            survived = int(summary["findings_passing_fdr"])
            tested = int(summary["findings_with_a_null_model"])
            alpha = summary["fdr_alpha"]

            # The second number is the honest one and the more interesting one.
            # Most findings here come from magnitude rules with no null model to
            # shuffle, so they are not testable even in principle -- and saying
            # "none survived" without saying "almost none were tested" invites
            # exactly the wrong reading.
            if tested:
                detail = (
                    f"Of {findings:,} patterns flagged, {tested:,} could be tested against "
                    f"a null model at all; these are the ones still standing at q ≤ {alpha}, "
                    "after correcting for every test the run performed."
                )
            else:
                detail = (
                    f"None of the {findings:,} patterns flagged could be tested against a "
                    "null model in this run, so none has been corrected for multiple "
                    "comparisons. That is a limit of the data, not a verdict on the "
                    "filings."
                )

            insights.append(
                {
                    "id": 4,
                    "icon": "🔍",
                    "title": "Findings that survive correction",
                    "description": f"{detail} A pattern is not a finding of wrongdoing.",
                    "value": f"{survived:,}",
                }
            )

    except Exception as e:
        # Insights are decorative — never let a query error crash the page.
        import logging

        logging.getLogger(__name__).warning("Error generating insights: %s", e)

    return insights
