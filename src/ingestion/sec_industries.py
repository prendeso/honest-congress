"""SEC industry codes for traded issuers.

Feeds `CompanyIndustry`, which `sectors.SectorIndex` reads to answer "what
sector is this holding in?" for every SEC registrant rather than the ~70
large-cap tickers somebody typed into a list by hand. That list is the binding
constraint on `sponsorship_conflict`, `bill_jurisdiction_conflict` and
`committee_jurisdiction_conflict` alike: all three join a bill or a committee
remit to a *sector*, so a member trading a mid-cap defense supplier is invisible
to every one of them.

Why the ugly endpoint
---------------------
`data.sec.gov/submissions/CIK*.json` is the clean way to get this and returns
**403** through this deployment's network policy. EDGAR's `browse-edgar` on
`www.sec.gov` is reachable, and with `output=atom` it returns parseable XML --
`assigned-sic`, `assigned-sic-desc`, `conformed-name` -- rather than HTML to
scrape. So that is the route.

Listing *by* SIC code is the other obvious direction and is a trap: EDGAR
enumerates every registrant that ever filed, so SIC 2834 alone runs past 1,600
entries, mostly long-dead shells. Per-company lookup is bounded by the tickers
that actually exist in the disclosures.

Cost and pacing
---------------
One request per ticker, paced under SEC's published 10 requests/second fair
access limit. Demand-driven by default -- only tickers present in
`transactions`, and only those not already cached -- so a realistic first run is
a few thousand requests and every run after it is a handful.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Set

import requests
from sqlalchemy.orm import Session

from src.analysis.sectors import sector_for_sic
from src.db.models import CompanyIndustry
from src.ingestion._helpers import traded_tickers
from src.ingestion.rate_limit import RequestBudgetExhausted, ThrottledClient
from src.ingestion.sec_tickers import TickerResolver, _user_agent

logger = logging.getLogger(__name__)

EDGAR_BASE_URL = "https://www.sec.gov"
BROWSE_PATH = "/cgi-bin/browse-edgar"

REQUEST_TIMEOUT = 45

# SEC publishes a fair-access limit of 10 requests/second. Sitting just under it
# keeps a long run polite and still finishes a few thousand tickers in minutes.
DEFAULT_REQUESTS_PER_SECOND = 8

_FIELDS = {
    "sic": re.compile(r"<assigned-sic>(\d+)</assigned-sic>"),
    "sic_description": re.compile(r"<assigned-sic-desc>(.*?)</assigned-sic-desc>", re.S),
    "company_name": re.compile(r"<conformed-name>(.*?)</conformed-name>", re.S),
}


def _unescape(value: str) -> str:
    """EDGAR double-escapes ampersands in the atom feed ("&amp;amp;")."""
    import html

    return html.unescape(html.unescape(value)).strip()


class EdgarCompanyClient(ThrottledClient):
    """Reads one company's filing header from EDGAR's browse endpoint."""

    base_url = EDGAR_BASE_URL
    name = "SEC EDGAR"

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        requests_per_second: int = DEFAULT_REQUESTS_PER_SECOND,
        max_requests: int | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            limit=requests_per_second,
            window_seconds=1,
            session=session,
            timeout=REQUEST_TIMEOUT,
            max_requests=max_requests,
            **kwargs,
        )

    def _auth_headers(self) -> Dict[str, str]:
        # SEC refuses a User-Agent with no contact email -- verified, it is a
        # 403 and not a soft failure. `_user_agent` builds it from settings.
        return {"User-Agent": _user_agent()}

    def industry(self, cik: int) -> Dict[str, str | None]:
        """The SIC code and description EDGAR has on file for a CIK."""
        text = self.get_text(
            BROWSE_PATH,
            {
                "action": "getcompany",
                "CIK": f"{cik:010d}",
                "type": "10-K",
                "dateb": "",
                "owner": "include",
                "count": "1",
                "output": "atom",
            },
        )
        found: Dict[str, str | None] = {}
        for field, pattern in _FIELDS.items():
            match = pattern.search(text)
            found[field] = _unescape(match.group(1)) if match else None
        return found

    def get_text(self, path: str, params: Dict[str, Any]) -> str:
        """Like `get`, but the response is XML rather than JSON.

        `ThrottledClient.get` calls `.json()`; this endpoint serves atom, so the
        rate limiting and retry behaviour are reused and only the decoding
        differs.
        """
        response = self._request(path, params)
        return response.text


def ingest_company_industries(
    db: Session,
    *,
    tickers: List[str] | None = None,
    all_registrants: bool = False,
    resolver: TickerResolver | None = None,
    client: EdgarCompanyClient | None = None,
    max_requests: int | None = None,
) -> Dict[str, Any]:
    """Look up and cache SEC industry codes for tickers not already cached."""
    client = client or EdgarCompanyClient(max_requests=max_requests)
    resolver = resolver or TickerResolver()

    cik_by_ticker = resolver.ciks()

    if tickers is not None:
        universe = {t.strip().upper() for t in tickers if t and t.strip()}
    elif all_registrants:
        universe = set(cik_by_ticker)
    else:
        universe = traded_tickers(db)

    cached = {row[0] for row in db.query(CompanyIndustry.ticker).all()}
    todo = sorted(universe - cached)

    if not todo:
        logger.info("Industry codes: nothing to look up (%d already cached)", len(cached))
        return _summary(db, client, looked_up=0, unknown_ticker=0, stopped_early=False)

    looked_up = 0
    unknown_ticker = 0
    stopped_early = False

    try:
        for ticker in todo:
            cik = cik_by_ticker.get(ticker)
            if cik is None:
                # Not an SEC registrant under this symbol: a fund, a foreign
                # listing, or a symbol the PDF parser misread. Recorded so it is
                # not looked up again on every run.
                db.add(CompanyIndustry(ticker=ticker))
                unknown_ticker += 1
                continue

            found = client.industry(cik)
            looked_up += 1
            sectors = sector_for_sic(found.get("sic"))
            db.add(
                CompanyIndustry(
                    ticker=ticker,
                    cik=cik,
                    sic=found.get("sic"),
                    sic_description=found.get("sic_description"),
                    company_name=found.get("company_name"),
                    # One sector per row: the map returns a set only for the
                    # handful of codes that genuinely span two, and the detectors
                    # re-derive the full set from `sic` anyway.
                    sector=sorted(sectors)[0] if sectors else None,
                )
            )

            if looked_up % 200 == 0:
                db.commit()
                logger.info("Industry codes: %d of %d looked up", looked_up, len(todo))

    except RequestBudgetExhausted as exc:
        logger.warning("Industry lookup stopped early: %s", exc)
        stopped_early = True

    db.commit()
    return _summary(db, client, looked_up, unknown_ticker, stopped_early)


def _summary(
    db: Session,
    client: EdgarCompanyClient,
    looked_up: int,
    unknown_ticker: int,
    stopped_early: bool,
) -> Dict[str, Any]:
    cached_total = db.query(CompanyIndustry).count()
    with_sector = db.query(CompanyIndustry).filter(CompanyIndustry.sector.isnot(None)).count()
    logger.info(
        "Industry codes: %d looked up, %d symbols not SEC registrants; "
        "%d of %d cached tickers now carry a sector, in %d requests",
        looked_up,
        unknown_ticker,
        with_sector,
        cached_total,
        client.requests_made,
    )
    return {
        "looked_up": looked_up,
        "not_sec_registrants": unknown_ticker,
        "cached_tickers": cached_total,
        "tickers_with_a_sector": with_sector,
        "requests_made": client.requests_made,
        "stopped_early": stopped_early,
    }


def uncached_tickers(db: Session) -> Set[str]:
    """Traded tickers with no industry lookup yet."""
    cached = {row[0] for row in db.query(CompanyIndustry.ticker).all()}
    return traded_tickers(db) - cached
