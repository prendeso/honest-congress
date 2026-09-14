"""Federal contract awards from USASpending.gov.

Feeds `GovernmentContract`, which `detect_contract_front_runs` reads to flag
purchases made shortly before an award to the same issuer.

USASpending is the government's official open-data source for federal spending,
public domain, no key and no registration. It publishes recipient *names*, so
tickers come from :mod:`src.ingestion.sec_tickers`.

Why it is driven by ticker
--------------------------
It used to fetch the three hundred largest contract actions in the window and
keep whichever happened to resolve to a ticker. Measured against the live API
for 2023-01-01 to 2026-09-14, that slice bottoms out at **$733,882,415** and
contains **fourteen** publicly traded companies -- BA, BAESY, CNC, FLR, GD, HII,
HON, HUM, LMT, MCK, NOC, RTX, SID, UNH. Every federal award below three-quarters
of a billion dollars was invisible to the detector, which is to say almost all of
them: the largest award action Microsoft received in that window is $56.6m, IBM
$131m, Booz Allen $270m, Caterpillar $87m, Pfizer $19m. None of those companies
could ever have produced a finding.

The docstring here used to justify the cut as skipping "thousands of small
purchase orders". That is not where the cut landed.

So it now runs the way the FEC and LDA ingesters already do, from the tickers
members have actually traded (`traded_tickers`), turned back into company names
through the SEC register (`TickerResolver.name_for`), one `recipient_search_text`
query each. Most tickers return nothing at all -- Apple, Netflix, Starbucks,
Nike, Target, Salesforce, Adobe, Eli Lilly and AMD each returned zero award
actions when measured -- which costs one request; the rest return a page.

The catch, and the guard
------------------------
`recipient_search_text` also matches through USASpending's own recipient
hierarchy, so asking about Leidos returns awards to "QTC MEDICAL SERVICES INC".
Those may well be real subsidiaries, but nothing in the SEC register confirms the
parentage and this project does not attribute an award to a company on a guess.
So every result is round-tripped through the same resolver and kept only if the
recipient name lands back on the ticker that was asked about -- the identical
guard `lda.py` applies to lobbying clients.

That guard is stricter here than it is there, and the cost is measured rather
than assumed. Across a 40-ticker sample it keeps 2,042 of 3,358 award actions,
and thirteen of the forty keep nothing at all, because the entity that holds the
federal business is named for a division: "CACI, INC. - FEDERAL", "DELL FEDERAL
SYSTEMS L.P", "CHEVRON USA INC.", "ORACLE AMERICA, INC", "MERCK SHARP & DOHME
LLC", "KBR WYLE SERVICES, LLC". Those are a known gap, not a silent one --
`rejected_wrong_company` counts them and the most common rejected names are
logged, so the gap is visible in every run summary. Closing it means writing each
one down in SUBSIDIARY_OVERRIDES, where it is a reviewable assertion.

Recipients that resolve to nothing at all are a different case and are correctly
dropped: national laboratories, universities and nonprofits take an enormous
share of federal contracting by value and none of them can be traded, so there is
no conflict for a trade detector to find.

One contract is not one award action
------------------------------------
Rows used to be keyed on USASpending's `internal_id`, which identifies the
CONTRACT. Nine of the transaction rows returned for Lockheed carried one
internal_id and six different action dates; over 500 rows the id had 437 distinct
values. Keying on it discarded 63 real obligations, and what the discarded ones
differ in is the action date -- the only field the front-run detector reads. See
`award_action_key`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List

import requests
from sqlalchemy.orm import Session

from src.db.models import GovernmentContract
from src.db.resilience import CONNECTION_LOSS_RETRIES, commit_or_recover
from src.ingestion._helpers import traded_tickers
from src.ingestion.rate_limit import (
    DEFAULT_BACKOFF_SECONDS,
    MAX_RETRIES,
    RETRYABLE_EXCEPTIONS,
    RETRYABLE_STATUS,
)
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

# How many rejected recipient names to name in the summary log. Enough to show
# the shape of the gap; not so many that a run log becomes a company directory.
REJECTED_NAMES_LOGGED = 10


def award_action_key(award: Dict[str, Any]) -> str | None:
    """A stable identifier for ONE contract action.

    `internal_id` is not it, and the difference is not academic. USASpending
    returns it on every transaction row and it looks like a row id, but it
    identifies the AWARD: contract W31P4Q24C0022 came back as nine transaction
    rows carrying one internal_id, with six distinct action dates from
    2024-06-28 to 2026-03-06 and nine distinct amounts. `generated_internal_id`
    is the same thing in a readable spelling -- 437 distinct values over 500
    transaction rows, both of them.

    Deduplicating on it discarded 63 of those 500 real award actions, and the
    ones discarded differ from the survivor precisely in ACTION DATE, which is
    the only field `detect_contract_front_runs` reads. A member who bought
    before the March 2026 obligation could not be flagged, because only the
    September 2025 one was stored.

    So the key is the natural one after all: the contract, the modification, the
    date and the amount. Measured over 1,200 live transaction rows it is unique
    on every one, and the modification number is what separates the case the
    previous comment worried about -- one contract modified twice on the same
    day for the same amount.

    Length is bounded well inside the column: 47 characters at the longest seen,
    and a FAR-maximum 50-character PIID puts the ceiling at 89. See
    tests/test_external_values_fit_columns.py.
    """
    award_id = str(award.get("Award ID") or "").strip()
    if not award_id:
        return None
    parts = (
        award_id,
        str(award.get("Mod") or "").strip(),
        str(award.get("Action Date") or "").strip()[:10],
        str(award.get("Transaction Amount") if award.get("Transaction Amount") is not None else ""),
    )
    return "|".join(parts)


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


def _post_with_retries(
    http: requests.Session,
    payload: Dict[str, Any],
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """POST to USASpending, retrying the failures that mean "try again".

    This feed never had any retry at all. Every other ingester routes through
    `ThrottledClient._request`, which grew a retry for transient statuses and
    then for transient exceptions; USASpending does not, because it needs POST
    and that client only speaks GET. So the whole of that work passed it by, and
    it kept failing on the first transient error like everything else used to:

        requests.exceptions.HTTPError: 502 Server Error: Bad Gateway for url:
        https://api.usaspending.gov/api/v2/search/spending_by_transaction/

    That is rebuild run 13 on 2026-09-14, and 502 is a status the shared list
    has retried since the Congress.gov 522 was fixed. Sixty-one minutes of
    contract ingestion ended on a code the project already knew to retry,
    because the code that knew it was somewhere this feed does not go.

    The policy is imported rather than restated. Which statuses and which
    exceptions are worth retrying is a judgement that took two goes to get right
    and must not now exist in two places.
    """
    last_failure = "no attempt was made"
    for attempt in range(MAX_RETRIES):
        try:
            response = http.post(SEARCH_URL, json=payload, timeout=REQUEST_TIMEOUT)
        except RETRYABLE_EXCEPTIONS as exc:
            last_failure = type(exc).__name__
        else:
            if response.status_code not in RETRYABLE_STATUS:
                # Anything else is a real answer, including a 4xx or a 500, and
                # `raise_for_status` is the right way to report it.
                response.raise_for_status()
                return response
            last_failure = f"HTTP {response.status_code}"

        if attempt == MAX_RETRIES - 1:
            break
        delay = DEFAULT_BACKOFF_SECONDS[min(attempt, len(DEFAULT_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "USASpending: %s; retrying in %ss (attempt %d of %d)",
            last_failure,
            delay,
            attempt + 1,
            MAX_RETRIES,
        )
        sleeper(delay)

    raise requests.exceptions.HTTPError(
        f"USASpending did not answer after {MAX_RETRIES} attempts ({last_failure})"
    )


def fetch_awards(
    start_date: str,
    end_date: str,
    *,
    recipient: str | None = None,
    pages: int = 1,
    session: requests.Session | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> List[Dict[str, Any]]:
    """Fetch contract award actions in a date range, largest first.

    `recipient` restricts the search to one company via USASpending's own
    `recipient_search_text` filter. Omitting it searches every recipient in the
    window, which is what this module used to do for the whole corpus -- see the
    module docstring for why one page of that is nearly useless to the detector.

    Sorted by award amount descending, so one page is "the hundred largest award
    actions this company received", which is a rule that can be stated on the
    site rather than an arbitrary slice.
    """
    http = session or requests.Session()
    awards: List[Dict[str, Any]] = []

    filters: Dict[str, Any] = {
        "award_type_codes": CONTRACT_AWARD_TYPES,
        "time_period": [{"start_date": start_date, "end_date": end_date}],
    }
    if recipient:
        filters["recipient_search_text"] = [recipient]

    for page in range(1, pages + 1):
        payload = {
            "filters": filters,
            "fields": [
                "Award ID",
                "Recipient Name",
                "Transaction Amount",
                "Action Date",
                "Awarding Agency",
                "Transaction Description",
                # The modification number. Two actions on one contract can share
                # a date and an amount and differ only here, so the key needs it.
                "Mod",
            ],
            "sort": "Transaction Amount",
            "order": "desc",
            "limit": PAGE_LIMIT,
            "page": page,
        }
        body = _post_with_retries(http, payload, sleeper=sleeper).json()

        results = body.get("results", [])
        awards.extend(results)

        if not body.get("page_metadata", {}).get("hasNext"):
            break

    return awards


def ingest_government_contracts(
    db: Session,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
    *,
    pages: int = 1,
    tickers: List[str] | None = None,
    resolver: TickerResolver | None = None,
) -> Dict[str, Any]:
    """Fetch federal awards for the companies members have traded, and store them."""
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    if start_date < EARLIEST_SEARCH_DATE:
        logger.warning(
            "USASpending award search starts at %s; raising start_date from %s",
            EARLIEST_SEARCH_DATE,
            start_date,
        )
        start_date = EARLIEST_SEARCH_DATE

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
            "fetched": 0,
            "imported": 0,
            "duplicates": 0,
            "money_taken_back": 0,
            "rejected_wrong_company": 0,
            "rejected_names": {},
            "connection_losses": 0,
            "companies_lost_to_the_database": [],
        }

    imported = 0
    duplicates = 0
    connection_losses = 0
    companies_lost_to_the_database: List[str] = []
    # Award actions that removed money from a contract or moved none at all.
    # Stored, because they happened and the table is the record of the feed, but
    # counted here because `detect_contract_front_runs` will not call any of them
    # an award -- see `award_action_criteria`.
    money_taken_back = 0
    rejected = 0
    unnamed = 0
    queried = 0
    fetched = 0
    rejected_names: Dict[str, int] = {}

    # Every key already stored, read once. This used to be one SELECT per award
    # to find out whether it was new, which was affordable at three hundred
    # awards and is not at the tens of thousands a per-company search returns --
    # `analyze` runs on a GitHub runner against a hosted database, so each one is
    # a network round trip, and on a nightly rerun EVERY award is a hit, so the
    # whole cost is paid to learn there is nothing to do.
    #
    # The set doubles as the within-run guard the `seen` set used to be: an id
    # added here on import is found here on the next iteration, which matters
    # because SessionLocal is autoflush=False and a pending row is invisible to
    # a query anyway.
    seen: set[str] = set()

    def reload_seen() -> None:
        """Refill `seen` from what is actually committed, in place.

        In place rather than rebound because `_store_one_companys_awards` closes
        over it. Called once to prime it, and again after any rollback -- at
        which point it is holding ids for rows that no longer exist, and would
        skip re-importing every one of them.
        """
        seen.clear()
        seen.update(
            row[0]
            for row in db.query(GovernmentContract.external_id)
            .filter(
                GovernmentContract.source == SOURCE,
                GovernmentContract.external_id.isnot(None),
            )
            .all()
        )

    reload_seen()

    def _store_one_companys_awards(ticker: str, awards: List[Dict[str, Any]]) -> Dict[str, int]:
        """Add one company's award actions. Counts are returned, not accumulated.

        Returned so a failed attempt contributes nothing: the caller folds them
        in only once the commit has actually happened. Called again on a retry,
        against a `seen` rebuilt from the committed state, so the rows the
        rollback removed are re-created rather than skipped.
        """
        counts = {"imported": 0, "duplicates": 0, "rejected": 0, "money_taken_back": 0}
        for award in awards:
            recipient_name = award.get("Recipient Name") or ""

            # The round trip. USASpending matched this award to the company we
            # asked about, possibly through its own recipient hierarchy, which
            # this project cannot verify. Keeping only names that resolve back
            # to the same ticker means every stored award is attributable from
            # the SEC register alone.
            if resolver.resolve(recipient_name) != ticker:
                counts["rejected"] += 1
                rejected_names[recipient_name] = rejected_names.get(recipient_name, 0) + 1
                continue

            # The date the award action actually happened -- the only one a
            # front-running window can be measured against.
            awarded = _parse_date(award.get("Action Date"))
            description = award.get("Transaction Description") or award.get("Award ID") or ""

            # What makes reruns idempotent, and what stops one contract's nine
            # separate obligations collapsing into one row. See
            # `award_action_key` for why USASpending's own `internal_id` cannot
            # do this job.
            external_id = award_action_key(award)
            if external_id is None:
                # No contract number, so nothing identifies this action and a
                # rerun could not recognise it. Counting it as a duplicate is
                # the honest arithmetic: it is not imported and it was not
                # rejected as the wrong company.
                counts["duplicates"] += 1
                continue
            if external_id in seen:
                counts["duplicates"] += 1
                continue
            seen.add(external_id)

            amount = _parse_amount(award.get("Transaction Amount"))
            if amount is not None and amount <= 0:
                counts["money_taken_back"] += 1

            db.add(
                GovernmentContract(
                    ticker=ticker,
                    agency=award.get("Awarding Agency"),
                    description=description,
                    amount=amount,
                    awarded_date=awarded,
                    source=SOURCE,
                    external_id=external_id,
                )
            )
            counts["imported"] += 1
        return counts

    for ticker in universe:
        company = resolver.name_for(ticker)
        if not company:
            # Not in the SEC register at all -- a foreign listing, a fund, or a
            # ticker the parser misread. Nothing to ask USASpending about.
            unnamed += 1
            continue

        queried += 1
        awards = fetch_awards(start_date, end_date, recipient=company, pages=pages)
        fetched += len(awards)

        # Committed per company rather than once at the end. The old shape held
        # one transaction open across the whole sweep -- 61 minutes in the run
        # that failed -- so a connection dropped at any point took every company
        # with it, and there was nothing to resume from. Now a drop costs one.
        #
        # The retry is free of the network: `awards` is already in memory, so
        # replaying it re-adds the same rows without asking USASpending again.
        for _attempt in range(1 + CONNECTION_LOSS_RETRIES):
            counts = _store_one_companys_awards(ticker, awards)
            if commit_or_recover(db, rebuild_caches=reload_seen, unit=ticker):
                imported += counts["imported"]
                duplicates += counts["duplicates"]
                rejected += counts["rejected"]
                money_taken_back += counts["money_taken_back"]
                break
            connection_losses += 1
        else:
            # Counted and named. A company silently missing from a run is
            # indistinguishable from one that holds no federal contracts.
            companies_lost_to_the_database.append(ticker)

    logger.info(
        "Government contracts: %d imported (%d of them deobligations or "
        "zero-dollar modifications, which the front-run detector will not treat "
        "as awards), %d duplicates, %d rejected as a different company, across "
        "%d tickers (%d not in the SEC register)",
        imported,
        money_taken_back,
        duplicates,
        rejected,
        queried,
        unnamed,
    )
    if connection_losses:
        logger.warning(
            "The database connection dropped %d time(s); %d company/companies were lost to it (%s)",
            connection_losses,
            len(companies_lost_to_the_database),
            ", ".join(companies_lost_to_the_database[:5]) or "none",
        )

    if rejected_names:
        # Named, not just counted. A recipient rejected a hundred times is a
        # listed contractor whose federal arm is registered under a divisional
        # name, and the only way that gap gets closed is by someone reading it
        # here and writing the assertion down in SUBSIDIARY_OVERRIDES.
        top = sorted(rejected_names.items(), key=lambda kv: -kv[1])[:REJECTED_NAMES_LOGGED]
        logger.info(
            "Most-rejected recipients (matched by USASpending's hierarchy, not "
            "by the SEC register): %s",
            "; ".join(f"{name} x{count}" for name, count in top),
        )

    return {
        "tickers_queried": queried,
        "tickers_without_a_registered_name": unnamed,
        "fetched": fetched,
        "imported": imported,
        "duplicates": duplicates,
        "money_taken_back": money_taken_back,
        "rejected_wrong_company": rejected,
        "rejected_names": rejected_names,
        "connection_losses": connection_losses,
        "companies_lost_to_the_database": companies_lost_to_the_database,
    }
