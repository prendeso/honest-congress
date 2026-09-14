"""Lobbying disclosures from the Senate Lobbying Disclosure Act database.

Feeds `LobbyingDisclosure`, which `detect_lobbying_overlaps` reads to flag
trades made close to a company's lobbying filing -- a market-wide signal that
the issuer is actively trying to shape policy.

The API is official, public domain, and works without a key. A free key from
https://lda.senate.gov/api/register/ raises the rate limit from roughly 15
requests/minute to 120, which is the only thing it changes: this module takes
the same path either way and simply paces itself accordingly.

Why it is driven by ticker
--------------------------
The LDA published **96,941 filings for 2024 alone**, and the API has no ticker
filter -- clients are names. Paging the whole year to find the tradeable ones
costs ~3,900 requests and throws away almost all of them.

So it runs from the other end: the tickers members have actually traded, turned
back into company names through the SEC register (`TickerResolver.name_for`),
one `client_name` query each. Most tickers return nothing, which costs a single
request; the rest return a handful of pages.

The catch, and the guard
------------------------
`client_name` is a substring match, which is mostly a gift -- asking for
"Lockheed Martin" also returns "LOCKHEED MARTIN AERONAUTIC SECTOR", a
subsidiary we want. But a short or generic registered name over-matches badly.
So every filing is round-tripped: the client name that comes back is resolved
through the same resolver, and the filing is kept only if it lands on the
ticker we asked about. Subsidiaries survive that; coincidences do not.

One wrinkle the real data exposed: filings made through an intermediary record
the client as "THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)" or
"535 GROUP, LLC ON BEHALF OF NORTHROP GRUMMAN SYSTEMS CORPORATION". A naive
round trip throws those away, and they are exactly the filings a
conflict-of-interest detector wants. `_candidate_client_names` pulls the
principal out before resolving.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterator, List, Set

import requests
from sqlalchemy.orm import Session

from src.db.models import LobbyingDisclosure
from src.ingestion._helpers import traded_tickers
from src.ingestion.rate_limit import (
    DEFAULT_BACKOFF_SECONDS,
    MINUTE,
    ThrottledClient,
)
from src.ingestion.sec_tickers import TickerResolver

logger = logging.getLogger(__name__)

# lda.senate.gov redirects here; using the canonical host avoids paying for a
# 301 on every request.
LDA_BASE_URL = "https://lda.gov/api/v1"

SOURCE = "senate_lda"
REQUEST_TIMEOUT = 60

# Published limits, with headroom. Anonymous callers are throttled hard enough
# that the difference is the difference between a minute and twenty.
# The documented anonymous limit is ~15/minute, but measured against the live
# API 12 still drew a steady stream of 429s -- the limit is per source address,
# and shared egress counts against it. 8 runs clean and stays polite; a key is
# the real answer if this is too slow.
ANONYMOUS_REQUESTS_PER_MINUTE = 8
AUTHENTICATED_REQUESTS_PER_MINUTE = 100

# The API caps page size at 25 regardless of what is asked for.
PAGE_SIZE = 25


class LDAClient(ThrottledClient):
    """Senate LDA client. The API key is optional and only changes the pace."""

    base_url = LDA_BASE_URL
    name = "Senate LDA"

    def __init__(
        self,
        api_key: str = "",
        *,
        session: requests.Session | None = None,
        requests_per_minute: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        self.api_key = api_key or ""

        if requests_per_minute is None:
            requests_per_minute = (
                AUTHENTICATED_REQUESTS_PER_MINUTE if self.api_key else ANONYMOUS_REQUESTS_PER_MINUTE
            )
        super().__init__(
            limit=requests_per_minute,
            window_seconds=MINUTE,
            session=session,
            timeout=REQUEST_TIMEOUT,
            sleeper=sleeper,
            clock=clock,
            backoff_seconds=backoff_seconds,
        )
        logger.info(
            "Senate LDA: %s, pacing at %d requests/minute",
            "authenticated" if self.api_key else "anonymous (no LDA_API_KEY set)",
            requests_per_minute,
        )

    def _auth_headers(self) -> Dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Token {self.api_key}"}
        return {}

    def filings(self, client_name: str, filing_year: int) -> Iterator[Dict[str, Any]]:
        """Every filing whose client name contains `client_name`, paged."""
        page = 1
        while True:
            body = self.get(
                "/filings/",
                {
                    "client_name": client_name,
                    "filing_year": filing_year,
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
            )
            results = body.get("results", [])
            yield from results
            if not body.get("next") or not results:
                return
            page += 1


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _parse_amount(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# "X (ON BEHALF OF Y)" and "X ON BEHALF OF Y" both appear; the parenthesised
# form also shows up as "(F.N.A. Y)" -- formerly known as -- where the name
# outside the parentheses is the one that matters, so only "on behalf of" is
# unwrapped.
_ON_BEHALF_OF = re.compile(r"\bon\s+behalf\s+of\b", re.IGNORECASE)


def _candidate_client_names(name: str) -> List[str]:
    """The client name, plus the principal it was filed on behalf of.

    Returned as alternatives rather than a replacement: either may be the
    company we asked about, and only one of them needs to be.
    """
    name = (name or "").strip()
    if not name:
        return []

    candidates = [name]
    match = _ON_BEHALF_OF.search(name)
    if match:
        principal = name[match.end() :].strip(" ()\t")
        if principal:
            candidates.append(principal)
    return candidates


def _issue_codes(filing: Dict[str, Any]) -> str | None:
    codes = []
    for activity in filing.get("lobbying_activities") or []:
        code = activity.get("general_issue_code")
        if code and code not in codes:
            codes.append(code)
    return ",".join(codes) or None


def ingest_lobbying_disclosures(
    db: Session,
    filing_year: int = 2024,
    *,
    api_key: str = "",
    tickers: List[str] | None = None,
    resolver: TickerResolver | None = None,
    client: LDAClient | None = None,
) -> Dict[str, Any]:
    """Ingest lobbying filings for the companies members have traded."""
    client = client or LDAClient(api_key)
    resolver = resolver or TickerResolver()

    universe = sorted(t.upper() for t in tickers) if tickers else sorted(traded_tickers(db))
    if not universe:
        logger.warning(
            "No transactions carry a ticker, so there is nothing to look up. "
            "Run `ingest` and `parse` first."
        )
        return {
            "tickers_queried": 0,
            "tickers_without_a_registered_name": 0,
            "imported": 0,
            "duplicates": 0,
            "rejected_wrong_company": 0,
            "requests_made": client.requests_made,
        }

    imported = 0
    duplicates = 0
    rejected = 0
    unnamed = 0
    queried = 0

    # Every filing_uuid already stored for this source, read once. This was a
    # SELECT per incoming filing, and the last run imported 4,920 of them --
    # one network round trip each, against a database on another host, and on a
    # rerun every single one is a hit, so the whole cost buys the answer
    # "nothing to do".
    #
    # The set doubles as the in-batch guard it sits beside: `SessionLocal` is
    # autoflush=False, so a row added earlier in this loop is invisible to a
    # query anyway.
    seen: Set[str] = {
        row[0]
        for row in db.query(LobbyingDisclosure.external_id)
        .filter(
            LobbyingDisclosure.source == SOURCE,
            LobbyingDisclosure.external_id.isnot(None),
        )
        .all()
    }

    for ticker in universe:
        company = resolver.name_for(ticker)
        if not company:
            # Not in the SEC register at all -- a foreign listing, a fund, or a
            # ticker the parser misread. Nothing to ask the LDA about.
            unnamed += 1
            continue

        queried += 1
        for filing in client.filings(company, filing_year):
            client_name = (filing.get("client") or {}).get("name") or ""

            # `client_name` is a substring match. It hands back subsidiaries,
            # which is wanted, and coincidental matches, which are not. The
            # round trip keeps the first and drops the second -- checking the
            # principal as well as the filing name, so a filing made through an
            # intermediary is not mistaken for a coincidence.
            if not any(
                resolver.resolve(candidate) == ticker
                for candidate in _candidate_client_names(client_name)
            ):
                rejected += 1
                continue

            external_id = str(filing.get("filing_uuid") or "") or None
            if external_id:
                if external_id in seen:
                    duplicates += 1
                    continue
                seen.add(external_id)
            else:
                # No filing_uuid. Rare enough to be worth one query rather than
                # a second pre-loaded set, and the semantics are preserved
                # exactly: a NULL external_id matches any other NULL one.
                if (
                    db.query(LobbyingDisclosure)
                    .filter(
                        LobbyingDisclosure.source == SOURCE,
                        LobbyingDisclosure.external_id.is_(None),
                    )
                    .first()
                ):
                    duplicates += 1
                    continue

            db.add(
                LobbyingDisclosure(
                    ticker=ticker,
                    registrant=(filing.get("registrant") or {}).get("name") or "",
                    client=client_name,
                    # A lobbying firm reports what the client paid it as
                    # `income`; a company lobbying for itself reports
                    # `expenses`. Exactly one is populated per filing.
                    amount=_parse_amount(filing.get("income"))
                    or _parse_amount(filing.get("expenses")),
                    filed_date=_parse_date(filing.get("dt_posted")),
                    issue_codes=_issue_codes(filing),
                    source=SOURCE,
                    external_id=external_id,
                )
            )
            imported += 1

    db.commit()

    logger.info(
        "Lobbying disclosures: %d imported, %d duplicates, %d rejected as a "
        "different company, across %d tickers in %d requests",
        imported,
        duplicates,
        rejected,
        queried,
        client.requests_made,
    )
    return {
        "tickers_queried": queried,
        "tickers_without_a_registered_name": unnamed,
        "imported": imported,
        "duplicates": duplicates,
        "rejected_wrong_company": rejected,
        "requests_made": client.requests_made,
    }
