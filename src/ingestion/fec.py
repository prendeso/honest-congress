"""Corporate PAC donations from the Federal Election Commission.

Feeds `CampaignDonation`, which `detect_donor_conflicts` reads to flag trades a
member made in a company that donated to their campaign.

The FEC API is official, public domain, and free -- a key from api.data.gov
costs nothing and takes a minute. What it is not is cheap to query, and the
shape of this module is entirely a response to that.

Why it runs backwards
---------------------
The obvious query is per member: "who donated to this campaign?" Measured
against the real API, one senator's principal committee has **10,428**
non-individual receipts for the 2024 cycle, most of them from joint-fundraising
and party committees that no ticker resolves. Across 537 members that is tens of
thousands of requests against a **1,000 requests/hour** budget, to end up
discarding almost all of it.

So it runs the other way. There were only **1,675 corporate PACs** active in the
2024 cycle -- the entire universe enumerates in 17 requests. Of those, 41%
resolve to a ticker, and the set is narrowed once more against the tickers
members have actually traded, because a donation in a company nobody in Congress
holds cannot produce a finding no matter how suspicious it looks. What survives
is a few dozen PACs, each one a single cheap keyset-paginated query.

The join key
------------
`unitedstates/congress-legislators` publishes an `id.fec` list for **537 of 539**
sitting legislators, so FEC candidate IDs map to `Member` rows exactly. No name
matching, no heuristics, and the two legislators without one are simply absent
rather than mis-attributed.

Recipients are restricted to **principal campaign committees** (designation "P").
Leadership PACs and joint-fundraising committees are associated with a member
too, but only loosely -- one committee routinely splits money across many
candidates -- and `CampaignDonation.member_id` means "donated to them", which
only a principal campaign committee states unambiguously.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Deque, Dict, Iterator, List, Set, Tuple

import requests
from sqlalchemy.orm import Session

from src.db.models import CampaignDonation, Member, Transaction
from src.ingestion.committees import BASE_URL as LEGISLATORS_BASE_URL
from src.ingestion.committees import _fetch_yaml
from src.ingestion.sec_tickers import TickerResolver

logger = logging.getLogger(__name__)

FEC_BASE_URL = "https://api.open.fec.gov/v1"
LEGISLATORS_URL = f"{LEGISLATORS_BASE_URL}/legislators-current.yaml"

SOURCE = "fec"
REQUEST_TIMEOUT = 60
PER_PAGE = 100

# api.data.gov allows 1,000 requests/hour on a default key. We leave headroom
# rather than sitting on the limit, because a run that trips it wastes the
# whole hour rather than just the request.
DEFAULT_REQUESTS_PER_HOUR = 900

# Candidate IDs per committees request. The endpoint accepts the filter
# repeated, which collapses ~800 lookups into ~16.
CANDIDATE_IDS_PER_REQUEST = 50

MAX_RETRIES = 4
DEFAULT_BACKOFF_SECONDS = (2, 4, 8, 16)


class RequestBudgetExhausted(RuntimeError):
    """Raised when a run hits its own `max_requests` cap.

    Not an error condition: a capped run is expected to stop early and be
    resumed. The caller catches this and reports what it managed to ingest.
    """


class RateLimiter:
    """Rolling-window limiter matching how api.data.gov actually counts.

    The quota is requests *per hour*, not a rate, so bursting is allowed and
    only the request that would cross the line has to wait. A fixed delay
    between calls would make a 200-request run take 13 minutes for no reason.
    """

    def __init__(
        self,
        requests_per_hour: int = DEFAULT_REQUESTS_PER_HOUR,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.requests_per_hour = requests_per_hour
        self._clock = clock
        self._sleeper = sleeper
        self._times: Deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        while self._times and now - self._times[0] >= 3600:
            self._times.popleft()

        if len(self._times) >= self.requests_per_hour:
            wait = 3600 - (now - self._times[0])
            logger.warning("FEC hourly quota reached; waiting %.0fs", wait)
            self._sleeper(wait)
            now = self._clock()
            while self._times and now - self._times[0] >= 3600:
                self._times.popleft()

        self._times.append(now)


class FECClient:
    """Thin FEC API client with a request budget and 429 handling."""

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        requests_per_hour: int = DEFAULT_REQUESTS_PER_HOUR,
        max_requests: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        backoff_seconds: Tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        if not api_key:
            raise ValueError(
                "FEC_API_KEY is not set. Get a free key at https://api.data.gov/signup/"
            )
        self.api_key = api_key
        self.session = session or requests.Session()
        self.max_requests = max_requests
        self.requests_made = 0
        self._sleeper = sleeper
        self._backoff = backoff_seconds
        self._limiter = RateLimiter(requests_per_hour, clock=clock, sleeper=sleeper)

    def get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.max_requests is not None and self.requests_made >= self.max_requests:
            raise RequestBudgetExhausted(
                f"stopped after {self.requests_made} requests (--max-requests)"
            )

        query = dict(params)
        query["api_key"] = self.api_key

        for attempt in range(MAX_RETRIES):
            self._limiter.acquire()
            self.requests_made += 1
            response = self.session.get(
                f"{FEC_BASE_URL}{path}", params=query, timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 429:
                # api.data.gov sends Retry-After on throttle. Honour it when
                # present; the table is only a fallback for when it is not.
                retry_after = response.headers.get("Retry-After")
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        pass
                logger.warning("FEC throttled (429); retrying in %ss", delay)
                self._sleeper(delay)
                continue

            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            return payload

        raise requests.exceptions.RetryError(f"FEC still throttling after {MAX_RETRIES} attempts")

    def paginate(self, path: str, params: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """Page through an endpoint that uses ordinary page numbers."""
        page = 1
        while True:
            body = self.get(path, {**params, "per_page": PER_PAGE, "page": page})
            yield from body.get("results", [])
            pages = body.get("pagination", {}).get("pages", 0)
            if page >= pages:
                return
            page += 1

    def paginate_keyset(self, path: str, params: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """Page through a schedule endpoint, which uses keyset pagination.

        The schedule endpoints do not accept `page`; they hand back
        `last_indexes`, which must be echoed into the next request. Passing
        `page` there silently returns the first page forever.
        """
        query = {**params, "per_page": PER_PAGE}
        while True:
            body = self.get(path, query)
            results = body.get("results", [])
            yield from results

            indexes = body.get("pagination", {}).get("last_indexes") or {}
            if not results or not indexes:
                return
            query = {**params, "per_page": PER_PAGE, **indexes}


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


def member_index_by_fec_id(db: Session) -> Dict[str, int]:
    """Map every FEC candidate ID to the `Member` row it belongs to.

    `legislators-current.yaml` carries `id.fec` for 537 of 539 sitting
    legislators, and a legislator who moved chambers has several. One request,
    no name matching.
    """
    members = {m.bioguide_id: m.id for m in db.query(Member).all()}
    index: Dict[str, int] = {}

    for person in _fetch_yaml(LEGISLATORS_URL) or []:
        ids = person.get("id", {}) or {}
        bioguide = ids.get("bioguide")
        if not bioguide:
            continue
        member_id = members.get(bioguide)
        if member_id is None:
            continue
        for fec_id in ids.get("fec") or []:
            index[fec_id] = member_id

    logger.info("Mapped %d FEC candidate IDs to members in the database", len(index))
    return index


def principal_committees(client: FECClient, candidate_ids: List[str]) -> Dict[str, str]:
    """Map principal-campaign-committee IDs to the candidate ID each serves.

    Keyed by committee because that is what a donation receipt names. One
    committee can list several candidate IDs for the same person -- a House
    member who later ran for Senate keeps both -- so the first one we were
    asked about wins, and all of them resolve to the same member anyway.

    The candidate filter is sent repeated rather than one ID per request,
    which the endpoint accepts: ~800 lookups collapse into ~16 requests.
    """
    mapping: Dict[str, str] = {}
    wanted = set(candidate_ids)

    for start in range(0, len(candidate_ids), CANDIDATE_IDS_PER_REQUEST):
        chunk = candidate_ids[start : start + CANDIDATE_IDS_PER_REQUEST]
        for committee in client.paginate(
            "/committees/", {"designation": "P", "candidate_id": chunk}
        ):
            for candidate_id in committee.get("candidate_ids") or []:
                if candidate_id in wanted:
                    mapping[committee["committee_id"]] = candidate_id
                    break

    logger.info("Mapped %d principal campaign committees", len(mapping))
    return mapping


def traded_tickers(db: Session) -> Set[str]:
    """Tickers that appear in at least one disclosed transaction."""
    rows = db.query(Transaction.ticker).filter(Transaction.ticker.isnot(None)).distinct().all()
    return {(t[0] or "").strip().upper() for t in rows if (t[0] or "").strip()}


def corporate_pacs(
    client: FECClient,
    resolver: TickerResolver,
    cycle: int,
    *,
    restrict_to: Set[str] | None = None,
) -> Dict[str, Tuple[str, str]]:
    """Corporate PACs that resolve to a ticker, as committee_id -> (ticker, name).

    `restrict_to` narrows the result to tickers somebody in Congress has
    actually traded. This is the difference between a few dozen follow-up
    queries and several hundred, and it costs nothing: a donation in a company
    no member holds cannot produce a donor-conflict finding.
    """
    pacs: Dict[str, Tuple[str, str]] = {}
    seen = 0

    for committee in client.paginate("/committees/", {"organization_type": "C", "cycle": cycle}):
        seen += 1
        name = committee.get("name") or ""
        ticker = resolver.resolve(name, political=True)
        if not ticker:
            continue
        if restrict_to is not None and ticker not in restrict_to:
            continue
        pacs[committee["committee_id"]] = (ticker, name)

    logger.info(
        "Corporate PACs: %d active in cycle %d, %d resolved to a traded ticker",
        seen,
        cycle,
        len(pacs),
    )
    return pacs


def ingest_campaign_donations(
    db: Session,
    api_key: str,
    cycle: int = 2024,
    *,
    resolver: TickerResolver | None = None,
    client: FECClient | None = None,
    max_requests: int | None = None,
    restrict_to_traded: bool = True,
    resume: bool = True,
) -> Dict[str, Any]:
    """Ingest corporate PAC donations to sitting members for one cycle.

    A full cycle is expensive -- roughly 16 requests per PAC against a
    1,000/hour budget -- so `max_requests` stops cleanly and `resume` (default)
    makes the next run skip the PACs already stored rather than re-paying for
    them. Pass `resume=False` to re-scan everything, which is what you want
    after a cycle's filings have been amended.
    """
    client = client or FECClient(api_key, max_requests=max_requests)
    resolver = resolver or TickerResolver()

    members_by_fec_id = member_index_by_fec_id(db)
    if not members_by_fec_id:
        logger.warning(
            "No members in the database have an FEC candidate ID. "
            "Run `ingest` first to populate the roster."
        )
        return {
            "pacs_queried": 0,
            "pacs_already_ingested": 0,
            "imported": 0,
            "duplicates": 0,
            "skipped_unmapped_recipient": 0,
            "requests_made": client.requests_made,
            "stopped_early": False,
        }

    traded = traded_tickers(db) if restrict_to_traded else None
    if restrict_to_traded and not traded:
        logger.warning(
            "No transactions carry a ticker, so every donation would be "
            "discarded. Falling back to the full corporate-PAC universe."
        )
        traded = None

    imported = 0
    duplicates = 0
    unmapped = 0
    pacs_queried = 0
    skipped_done = 0
    stopped_early = False
    seen_sub_ids: Set[str] = set()

    try:
        committees = principal_committees(client, sorted(members_by_fec_id))
        pacs = corporate_pacs(client, resolver, cycle, restrict_to=traded)

        # A capped or throttled run stops partway through, so resuming must not
        # re-spend requests on PACs already done. Committees are processed in a
        # stable order and any ticker already carrying donations for this cycle
        # is skipped -- one query to make the next run cheap.
        already_done = {
            row[0]
            for row in db.query(CampaignDonation.ticker)
            .filter(
                CampaignDonation.cycle == str(cycle),
                CampaignDonation.source == SOURCE,
            )
            .distinct()
            .all()
        }

        for committee_id, (ticker, pac_name) in sorted(pacs.items()):
            if resume and ticker in already_done:
                skipped_done += 1
                continue
            pacs_queried += 1
            for receipt in client.paginate_keyset(
                "/schedules/schedule_a/",
                {
                    "contributor_id": committee_id,
                    "two_year_transaction_period": cycle,
                    # Narrowed server-side rather than after the fact. Only
                    # principal campaign committees resolve to a member here,
                    # and dropping the rest saves whole pages: Boeing's PAC
                    # goes from 1,443 receipts to 1,231, and every page is a
                    # request against a 1,000/hour budget.
                    "recipient_committee_designation": "P",
                    "recipient_committee_type": ["H", "S"],
                },
            ):
                # Memo entries restate a transaction reported elsewhere. Counting
                # them would double the donation totals for every PAC that uses
                # them.
                if receipt.get("memo_code"):
                    continue

                amount = _parse_amount(receipt.get("contribution_receipt_amount"))
                if amount is None or amount <= 0:
                    continue  # refunds and reattributions are not donations

                candidate_id = committees.get(receipt.get("committee_id") or "")
                member_id = members_by_fec_id.get(candidate_id or "")
                if member_id is None:
                    # Party, leadership and joint-fundraising committees, and
                    # candidates who are not sitting members.
                    unmapped += 1
                    continue

                # FEC's own transaction id. The natural key is not unique in
                # the real data -- Boeing's PAC gave one committee $5,000 twice
                # on 2024-12-31, primary and general -- so deduplicating on
                # (member, ticker, date, amount) silently merges real donations.
                sub_id = str(receipt.get("sub_id") or "") or None
                if sub_id and sub_id in seen_sub_ids:
                    duplicates += 1
                    continue
                if sub_id:
                    seen_sub_ids.add(sub_id)

                donated = _parse_date(receipt.get("contribution_receipt_date"))
                exists = (
                    db.query(CampaignDonation)
                    .filter(
                        CampaignDonation.source == SOURCE,
                        CampaignDonation.external_id == sub_id,
                    )
                    .first()
                )
                if exists:
                    duplicates += 1
                    continue

                db.add(
                    CampaignDonation(
                        member_id=member_id,
                        ticker=ticker,
                        donor_name=receipt.get("contributor_name") or pac_name,
                        amount=amount,
                        cycle=str(cycle),
                        transaction_type=receipt.get("receipt_type_desc"),
                        donation_date=donated,
                        source=SOURCE,
                        external_id=sub_id,
                    )
                )
                imported += 1

    except RequestBudgetExhausted as exc:
        # Not a failure. Everything ingested so far is committed below and the
        # next run picks up where this one stopped.
        logger.warning("FEC ingest stopped early: %s", exc)
        stopped_early = True

    db.commit()

    logger.info(
        "Campaign donations: %d imported, %d duplicates, %d receipts to "
        "committees that map to no sitting member, across %d PACs in %d requests",
        imported,
        duplicates,
        unmapped,
        pacs_queried,
        client.requests_made,
    )
    return {
        "pacs_queried": pacs_queried,
        "pacs_already_ingested": skipped_done,
        "imported": imported,
        "duplicates": duplicates,
        "skipped_unmapped_recipient": unmapped,
        "requests_made": client.requests_made,
        "stopped_early": stopped_early,
    }
