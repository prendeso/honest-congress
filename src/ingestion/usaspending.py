"""Federal contract awards from USASpending.gov.

Feeds `GovernmentContract`, which `detect_contract_front_runs` reads to flag
purchases made shortly before an award to the same issuer.

USASpending is the government's official open-data source for federal spending,
public domain, no key and no registration. It publishes recipient *names*, so
tickers come from :mod:`src.ingestion.sec_tickers`.

Awards whose recipient does not resolve to a ticker are skipped, and that is
correct rather than lossy: national laboratories, universities and nonprofits
take an enormous share of federal contracting by value and none of them can be
traded, so there is no conflict for a trade detector to find. What the resolver
must not miss is a listed parent behind a subsidiary name -- see
SUBSIDIARY_OVERRIDES there.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

import requests
from sqlalchemy.orm import Session

from src.db.models import GovernmentContract
from src.ingestion.sec_tickers import TickerResolver

logger = logging.getLogger(__name__)

# Transaction level, not award level. `spending_by_award` returns the PARENT
# award with its original base date, so a filter on June 2024 still hands back
# a Lockheed contract dated 1993-10-15 -- useless for a detector asking what was
# traded shortly before an award. `spending_by_transaction` returns individual
# award actions with the date each one happened.
SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"

# Contract award types: A/B/C/D are the definitive contract vehicles.
CONTRACT_AWARD_TYPES = ["A", "B", "C", "D"]

SOURCE = "usaspending"
REQUEST_TIMEOUT = 90
PAGE_LIMIT = 100

# USASpending caps award search at 2007-10-01; anything earlier needs the bulk
# download endpoints instead.
EARLIEST_SEARCH_DATE = "2007-10-01"


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def fetch_awards(
    start_date: str,
    end_date: str,
    *,
    pages: int = 3,
    session: requests.Session | None = None,
) -> List[Dict[str, Any]]:
    """Fetch contract awards in a date range, largest first.

    Sorted by award amount because the detector cares about material awards and
    the tail is enormous -- there is no value in paging through thousands of
    small purchase orders.
    """
    http = session or requests.Session()
    awards: List[Dict[str, Any]] = []

    for page in range(1, pages + 1):
        payload = {
            "filters": {
                "award_type_codes": CONTRACT_AWARD_TYPES,
                "time_period": [{"start_date": start_date, "end_date": end_date}],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Transaction Amount",
                "Action Date",
                "Awarding Agency",
                "Transaction Description",
            ],
            "sort": "Transaction Amount",
            "order": "desc",
            "limit": PAGE_LIMIT,
            "page": page,
        }
        response = http.post(SEARCH_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = response.json()

        results = body.get("results", [])
        awards.extend(results)

        if not body.get("page_metadata", {}).get("hasNext"):
            break

    logger.info("Fetched %d contract award actions from USASpending", len(awards))
    return awards


def ingest_government_contracts(
    db: Session,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
    *,
    pages: int = 3,
    resolver: TickerResolver | None = None,
) -> Dict[str, int]:
    """Fetch awards and upsert those whose recipient resolves to a ticker."""
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    if start_date < EARLIEST_SEARCH_DATE:
        logger.warning(
            "USASpending award search starts at %s; raising start_date from %s",
            EARLIEST_SEARCH_DATE,
            start_date,
        )
        start_date = EARLIEST_SEARCH_DATE

    resolver = resolver or TickerResolver()
    awards = fetch_awards(start_date, end_date, pages=pages)

    imported = 0
    unresolved = 0
    duplicates = 0
    seen: set[str] = set()

    for award in awards:
        recipient = award.get("Recipient Name") or ""
        ticker = resolver.resolve(recipient)
        if not ticker:
            unresolved += 1
            continue

        # The date the award action actually happened -- the only one a
        # front-running window can be measured against.
        awarded = _parse_date(award.get("Action Date"))
        description = award.get("Transaction Description") or award.get("Award ID") or ""

        # `internal_id` is USASpending's own identifier for the award ACTION.
        # It is returned whether or not it is listed in `fields`, and it is what
        # makes reruns idempotent. The natural key cannot do this
        # job: one contract is routinely modified several times on the same day
        # for the same amount, and collapsing those loses real award activity.
        external_id = str(award.get("internal_id") or "") or None
        if external_id and external_id in seen:
            duplicates += 1
            continue
        if external_id:
            seen.add(external_id)

        exists = (
            db.query(GovernmentContract)
            .filter(
                GovernmentContract.source == SOURCE,
                GovernmentContract.external_id == external_id,
            )
            .first()
            if external_id
            else db.query(GovernmentContract)
            .filter(
                GovernmentContract.ticker == ticker,
                GovernmentContract.awarded_date == awarded,
                GovernmentContract.description == description,
            )
            .first()
        )
        if exists:
            duplicates += 1
            continue

        db.add(
            GovernmentContract(
                ticker=ticker,
                agency=award.get("Awarding Agency"),
                description=description,
                amount=_parse_amount(award.get("Transaction Amount")),
                awarded_date=awarded,
                source=SOURCE,
                external_id=external_id,
            )
        )
        imported += 1

    db.commit()

    logger.info(
        "Government contracts: %d imported, %d duplicates, %d recipients "
        "not publicly traded (expected -- labs, universities, nonprofits)",
        imported,
        duplicates,
        unresolved,
    )
    return {
        "imported": imported,
        "duplicates": duplicates,
        "unresolved_recipients": unresolved,
        "fetched": len(awards),
    }
