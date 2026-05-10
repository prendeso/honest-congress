"""QuiverQuant API client for congressional trading data."""

import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType
from src.ingestion.date_utils import choose_filing_date, choose_transaction_date

# Load .env file
load_dotenv()

logger = logging.getLogger(__name__)

# QuiverQuant API endpoints (Tier 1)
QUIVERQUANT_BASE = "https://api.quiverquant.com/beta"

# Bulk endpoint - Full historical data (ALL members from Congress: House + Senate, active + retired)
BULK_CONGRESS_ENDPOINT = f"{QUIVERQUANT_BASE}/bulk/congresstrading"

# Historical endpoints - Full historical data by chamber
HISTORICAL_HOUSE_ENDPOINT = f"{QUIVERQUANT_BASE}/historical/housetrading"
HISTORICAL_SENATE_ENDPOINT = f"{QUIVERQUANT_BASE}/historical/senatetrading"
HISTORICAL_CONGRESS_ENDPOINT = f"{QUIVERQUANT_BASE}/historical/congresstrading"

# Live endpoints - Recent data only
LIVE_HOUSE_ENDPOINT = f"{QUIVERQUANT_BASE}/live/housetrading"
LIVE_SENATE_ENDPOINT = f"{QUIVERQUANT_BASE}/live/senatetrading"
LIVE_CONGRESS_ENDPOINT = f"{QUIVERQUANT_BASE}/live/congresstrading"

# Tier-2 datasets (drive new detectors)
BULK_DONORS_ENDPOINT = f"{QUIVERQUANT_BASE}/bulk/corporatedonors"
HISTORICAL_LOBBYING_ENDPOINT = f"{QUIVERQUANT_BASE}/historical/lobbying"  # /{ticker}
LIVE_LOBBYING_ENDPOINT = f"{QUIVERQUANT_BASE}/live/lobbying"
HISTORICAL_CONTRACTS_ENDPOINT = f"{QUIVERQUANT_BASE}/historical/govcontractsall"  # /{ticker}
LIVE_CONTRACTS_ENDPOINT = f"{QUIVERQUANT_BASE}/live/govcontractsall"

# Tier-3 context endpoints (no DB persistence — used to enrich existing detectors)
LIVE_INSIDERS_ENDPOINT = f"{QUIVERQUANT_BASE}/live/insiders"
LIVE_SEC13F_ENDPOINT = f"{QUIVERQUANT_BASE}/live/sec13f"
LIVE_SEC13F_CHANGES_ENDPOINT = f"{QUIVERQUANT_BASE}/live/sec13fchanges"
LIVE_TOP_SHAREHOLDERS_ENDPOINT = f"{QUIVERQUANT_BASE}/live/topshareholders"  # /{ticker}
LIVE_NEWS_ENDPOINT = f"{QUIVERQUANT_BASE}/live/quivernews"

# Backoff schedule for HTTP 429 / transient 5xx responses (seconds).
_RETRY_BACKOFF_SECONDS = (1, 2, 4, 8)

# Maps the single-letter Party codes QuiverQuant returns to our Party enum.
# Falls back to OTHER for anything unrecognized.
_PARTY_BY_LETTER = {
    "D": Party.DEMOCRAT,
    "R": Party.REPUBLICAN,
    "I": Party.INDEPENDENT,
    "O": Party.OTHER,
}

# Match "$1,001 - $15,000" (with optional whitespace, optional $ on either side).
_AMOUNT_RANGE_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*-\s*\$?\s*([\d,]+(?:\.\d+)?)")


def parse_amount_range(amount_str: str | None) -> tuple[float | None, float | None]:
    """Parse QuiverQuant `Amount` strings into (min, max).

    Handles three shapes:
    * Range:  "$1,001 - $15,000"        -> (1001.0, 15000.0)
    * Single: "50000" or "$50,000"      -> (50000.0, 50000.0)
    * Empty/garbage:                    -> (None, None)

    Critical: the previous implementation tried `float()` directly on the
    raw value, so range-format strings (the dominant House-PTR shape) hit
    a ``ValueError`` and both bounds ended up ``None``. That silently broke
    the ``large_trade`` and ``late_filing`` detectors for QuiverQuant
    data — they both branch on ``amount_min`` / ``amount_max``.
    """
    if not amount_str:
        return None, None

    s = str(amount_str).strip()
    if not s:
        return None, None

    m = _AMOUNT_RANGE_RE.search(s)
    if m:
        try:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except (ValueError, TypeError):
            return None, None

    # Single value (possibly with $ and commas).
    cleaned = s.replace("$", "").replace(",", "").strip()
    try:
        v = float(cleaned)
        return v, v
    except (ValueError, TypeError):
        return None, None


class QuiverQuantClient:
    """Client for QuiverQuant congressional trading API."""

    def __init__(self, api_key: str | None = None):
        """Initialize QuiverQuant client."""
        self.api_key = api_key or os.getenv("QUIVERQUANT_API_KEY")

        if not self.api_key:
            raise ValueError(
                "QuiverQuant API key not provided. "
                "Set QUIVERQUANT_API_KEY environment variable or pass api_key parameter."
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "HonestCongress/1.0",
                "Authorization": f"Bearer {self.api_key}",
            }
        )
        self.delay = 0.5  # Rate limiting

    # =========================================================================
    # BULK ENDPOINT - Recommended for complete coverage
    # =========================================================================

    def get_bulk_congress_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """
        Fetch ALL congressional trades (House + Senate, active + retired members).
        This is the recommended endpoint for comprehensive historical data.
        """
        return self._fetch_trades(BULK_CONGRESS_ENDPOINT, limit)

    # =========================================================================
    # HISTORICAL ENDPOINTS - Full historical data by chamber
    # =========================================================================

    def get_historical_house_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch historical House trading data (all members)."""
        return self._fetch_trades(HISTORICAL_HOUSE_ENDPOINT, limit)

    def get_historical_senate_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch historical Senate trading data (all members)."""
        return self._fetch_trades(HISTORICAL_SENATE_ENDPOINT, limit)

    def get_historical_congress_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch historical Congress trading data (House + Senate)."""
        return self._fetch_trades(HISTORICAL_CONGRESS_ENDPOINT, limit)

    # =========================================================================
    # LIVE ENDPOINTS - Recent data only
    # =========================================================================

    def get_live_house_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch recent House trades."""
        return self._fetch_trades(LIVE_HOUSE_ENDPOINT, limit)

    def get_live_senate_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch recent Senate trades."""
        return self._fetch_trades(LIVE_SENATE_ENDPOINT, limit)

    def get_live_congress_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        """Fetch recent Congress trades."""
        return self._fetch_trades(LIVE_CONGRESS_ENDPOINT, limit)

    # Legacy aliases
    def get_house_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        return self.get_historical_house_trades(limit)

    def get_senate_trades(self, limit: int | None = None) -> List[Dict[str, Any]]:
        return self.get_historical_senate_trades(limit)

    # =========================================================================
    # TIER 2 ENDPOINTS — corporate donors, lobbying, government contracts
    # =========================================================================

    def get_corporate_donors(
        self,
        bioguide_id: str | None = None,
        ticker: str | None = None,
        cycle: str | None = None,
        limit: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Fetch corporate-to-member campaign donations.

        At least one of ``bioguide_id`` or ``ticker`` should be provided —
        without filters this returns the entire dataset which is large.
        """
        params: Dict[str, str] = {}
        if bioguide_id:
            params["bioguide_id"] = bioguide_id
        if ticker:
            params["ticker"] = ticker
        if cycle:
            params["cycle"] = cycle
        return self._fetch_trades(BULK_DONORS_ENDPOINT, limit, params=params or None)

    def get_lobbying(
        self, ticker: str | None = None, limit: int | None = None
    ) -> List[Dict[str, Any]]:
        """Fetch lobbying disclosures.

        With ``ticker`` set, fetches the per-ticker historical endpoint;
        without, fetches the live (recent only) feed.
        """
        endpoint = f"{HISTORICAL_LOBBYING_ENDPOINT}/{ticker}" if ticker else LIVE_LOBBYING_ENDPOINT
        return self._fetch_trades(endpoint, limit)

    def get_government_contracts(
        self, ticker: str | None = None, limit: int | None = None
    ) -> List[Dict[str, Any]]:
        """Fetch federal contract awards.

        With ``ticker`` set, fetches per-ticker historical; without, live.
        """
        endpoint = (
            f"{HISTORICAL_CONTRACTS_ENDPOINT}/{ticker}" if ticker else LIVE_CONTRACTS_ENDPOINT
        )
        return self._fetch_trades(endpoint, limit)

    # =========================================================================
    # TIER 3 ENDPOINTS — context-only, no DB persistence by default. Use
    # these from detectors / scripts when you need supplementary data on a
    # specific ticker or holding.
    # =========================================================================

    def get_insiders(
        self, ticker: str | None = None, limit: int | None = None
    ) -> List[Dict[str, Any]]:
        """SEC Form 4 insider transactions. Optional ?ticker= filter."""
        params = {"ticker": ticker} if ticker else None
        return self._fetch_trades(LIVE_INSIDERS_ENDPOINT, limit, params=params)

    def get_sec13f(
        self, ticker: str | None = None, owner: str | None = None, limit: int | None = None
    ) -> List[Dict[str, Any]]:
        """Hedge fund 13F holdings. Filterable by ticker or fund owner."""
        params: Dict[str, str] = {}
        if ticker:
            params["ticker"] = ticker
        if owner:
            params["owner"] = owner
        return self._fetch_trades(LIVE_SEC13F_ENDPOINT, limit, params=params or None)

    def get_sec13f_changes(
        self, ticker: str | None = None, owner: str | None = None, limit: int | None = None
    ) -> List[Dict[str, Any]]:
        """Quarter-over-quarter changes in hedge fund 13F holdings."""
        params: Dict[str, str] = {}
        if ticker:
            params["ticker"] = ticker
        if owner:
            params["owner"] = owner
        return self._fetch_trades(LIVE_SEC13F_CHANGES_ENDPOINT, limit, params=params or None)

    def get_top_shareholders(self, ticker: str, limit: int | None = None) -> List[Dict[str, Any]]:
        """Top shareholders of a ticker (by reported holdings)."""
        return self._fetch_trades(f"{LIVE_TOP_SHAREHOLDERS_ENDPOINT}/{ticker}", limit)

    def get_news(
        self, ticker: str | None = None, page: int | None = None, page_size: int | None = None
    ) -> List[Dict[str, Any]]:
        """Stock news feed with timestamps. Useful for trade-timing analysis."""
        params: Dict[str, str] = {}
        if ticker:
            params["ticker"] = ticker
        if page is not None:
            params["page"] = str(page)
        if page_size is not None:
            params["page_size"] = str(page_size)
        return self._fetch_trades(LIVE_NEWS_ENDPOINT, None, params=params or None)

    # =========================================================================
    # INTERNAL METHODS
    # =========================================================================

    def _fetch_trades(
        self,
        endpoint: str,
        limit: int | None,
        params: Dict[str, str] | None = None,
    ) -> List[Dict[str, Any]]:
        """Fetch trades from an endpoint, retrying on 429 / transient 5xx.

        Previously a 429 just logged and returned an empty list, so the
        daily cron would silently succeed with zero rows. Now we honor a
        ``Retry-After`` header when present, otherwise fall back to a
        small exponential backoff, before giving up.
        """
        for attempt, wait_default in enumerate(_RETRY_BACKOFF_SECONDS, start=1):
            try:
                logger.info("Fetching trades from %s (attempt %d)", endpoint, attempt)
                response = self.session.get(endpoint, timeout=60, params=params)
            except requests.RequestException as e:
                logger.warning("Network error fetching %s: %s", endpoint, e)
                if attempt < len(_RETRY_BACKOFF_SECONDS):
                    time.sleep(wait_default)
                    continue
                return []

            status = response.status_code
            if status == 200:
                try:
                    data = response.json()
                except ValueError as e:
                    logger.error("Non-JSON response from %s: %s", endpoint, e)
                    return []
                if not isinstance(data, list):
                    logger.warning("Unexpected response shape from %s: %s", endpoint, type(data))
                    return []
                trades = data[:limit] if limit else data
                logger.info("Fetched %d trades from %s", len(trades), endpoint)
                return trades

            if status == 401:
                logger.error("QuiverQuant rejected the API key (401). Not retrying.")
                return []

            if status == 429 or 500 <= status < 600:
                wait = wait_default
                ra = response.headers.get("Retry-After")
                if ra:
                    try:
                        wait = max(wait, int(ra))
                    except (TypeError, ValueError):
                        pass
                if attempt < len(_RETRY_BACKOFF_SECONDS):
                    logger.warning(
                        "%s returned %d; retrying in %ds (attempt %d)",
                        endpoint,
                        status,
                        wait,
                        attempt,
                    )
                    time.sleep(wait)
                    continue
                logger.error(
                    "%s returned %d after %d attempts; giving up", endpoint, status, attempt
                )
                return []

            logger.error("Unexpected HTTP %d from %s", status, endpoint)
            return []

        return []

    def ingest_trades(
        self,
        db: Session,
        chamber: str = "both",
        source: str = "quiverquant",
        mode: str = "bulk",
    ) -> Dict[str, int]:
        """
        Ingest congressional trades into database.

        Args:
            db: Database session
            chamber: "house", "senate", "congress", or "both"
            source: Source identifier
            mode: "bulk" (recommended), "historical", or "live"

        Returns:
            Dict with imported, duplicates, errors counts
        """
        results = {"imported": 0, "duplicates": 0, "errors": 0}

        # Normalize chamber
        if chamber == "both":
            chamber = "congress"

        logger.info(f"Starting trade ingestion: mode={mode}, chamber={chamber}")

        # Fetch trades based on mode
        all_trades = []

        if mode == "bulk":
            # Use bulk endpoint for ALL members (House + Senate, active + retired)
            logger.info("Using bulk congress endpoint (full history, all members)")
            all_trades = self.get_bulk_congress_trades()

        elif mode == "historical":
            if chamber == "house":
                all_trades = self.get_historical_house_trades()
            elif chamber == "senate":
                all_trades = self.get_historical_senate_trades()
            else:
                all_trades = self.get_historical_house_trades()
                time.sleep(self.delay)
                all_trades.extend(self.get_historical_senate_trades())

        elif mode == "live":
            if chamber == "house":
                all_trades = self.get_live_house_trades()
            elif chamber == "senate":
                all_trades = self.get_live_senate_trades()
            else:
                all_trades = self.get_live_house_trades()
                time.sleep(self.delay)
                all_trades.extend(self.get_live_senate_trades())

        else:
            logger.error(f"Unknown mode: {mode}")
            return results

        logger.info(f"Processing {len(all_trades)} trades")

        # Process each trade
        for trade_data in all_trades:
            try:
                result = self._ingest_trade(db, trade_data, source)
                if result == "imported":
                    results["imported"] += 1
                elif result == "duplicate":
                    results["duplicates"] += 1
                else:
                    results["errors"] += 1
            except Exception as e:
                logger.error(f"Error ingesting trade: {e}")
                results["errors"] += 1

        db.commit()
        logger.info(f"Ingestion complete: {results}")
        return results

    def _ingest_trade(self, db: Session, trade_data: Dict[str, Any], source: str) -> str:
        """Ingest a single trade record. Returns 'imported', 'duplicate', or 'error'."""
        try:
            # Extract member name (bulk uses "Name", live uses "Representative"/"Senator")
            member_name = (
                trade_data.get("Name")
                or trade_data.get("Representative")
                or trade_data.get("Senator")
            )
            if not member_name:
                return "error"

            member_name = member_name.strip()

            # Extract trade fields
            ticker = trade_data.get("Ticker", "").upper().strip()
            transaction_str = trade_data.get("Transaction", "").lower()
            amount_str = trade_data.get("Amount", "")
            date_str = trade_data.get("Traded") or trade_data.get("Date")
            filed_str = trade_data.get("Filed")
            last_modified_str = trade_data.get("last_modified")

            # Validate required fields
            if not all([member_name, ticker, transaction_str]):
                return "error"

            # Use filed date if trade date missing
            if not date_str:
                date_str = filed_str or last_modified_str

            # Map transaction type
            if "purchase" in transaction_str or "buy" in transaction_str:
                txn_type = TransactionType.PURCHASE
            elif "sale" in transaction_str or "sell" in transaction_str:
                txn_type = TransactionType.SALE
            elif "exchange" in transaction_str:
                txn_type = TransactionType.EXCHANGE
            else:
                return "error"

            # Parse dates
            transaction_date = self._parse_date(date_str)
            filed_date = self._parse_date(filed_str or last_modified_str or date_str)

            if not transaction_date:
                return "error"

            safe_transaction_date = choose_transaction_date(transaction_date, filed_date)
            safe_filed_date = choose_filing_date(filed_date, safe_transaction_date.year)

            # Find member, preferring the stable BioGuideID over name match.
            # Auto-creation is only allowed when we have a BioGuideID — name-
            # only matches risk creating duplicate members for the same person
            # under different naming conventions ("Bob Smith" vs "Robert Smith Jr.").
            bioguide_id = trade_data.get("BioGuideID")
            member = None

            if bioguide_id:
                member = db.query(Member).filter(Member.bioguide_id == bioguide_id).first()
                if not member:
                    member = self._create_member_from_trade(db, trade_data)
            else:
                member = self._find_member(db, member_name)
                if not member:
                    logger.warning(
                        "Skipping unmatched trade: name=%r ticker=%s (no BioGuideID and no DB match)",
                        member_name,
                        ticker,
                    )
                    return "error"

            if not member:
                return "error"

            # Check for duplicates
            existing = (
                db.query(Transaction)
                .filter(
                    Transaction.ticker == ticker,
                    Transaction.transaction_type == txn_type,
                    Transaction.transaction_date == transaction_date,
                )
                .filter(Transaction.disclosure.has(Disclosure.member_id == member.id))
                .first()
            )

            if existing:
                return "duplicate"

            # Create or find disclosure record
            disclosure = (
                db.query(Disclosure)
                .filter(
                    Disclosure.member_id == member.id,
                    Disclosure.filing_year == safe_transaction_date.year,
                    Disclosure.document_id.like(f"QANT_{member.id}_%"),
                )
                .first()
            )

            if not disclosure:
                disclosure = Disclosure(
                    member_id=member.id,
                    filing_year=safe_transaction_date.year,
                    filing_type="T",
                    filing_date=safe_filed_date,
                    document_id=f"QANT_{member.id}_{safe_transaction_date.year}",
                    document_url="",
                    is_ptr=True,
                    parsed=True,
                )
                db.add(disclosure)
                db.flush()

            # Parse amount — handles range, single, and empty cases. The
            # House PTR `Amount` is virtually always a range string like
            # "$1,001 - $15,000"; the previous direct float() cast would
            # raise ValueError and leave both bounds None, which silently
            # broke downstream detectors keyed on amount.
            amount_min, amount_max = parse_amount_range(amount_str)

            # Create transaction
            transaction = Transaction(
                disclosure_id=disclosure.id,
                transaction_date=safe_transaction_date,
                transaction_type=txn_type,
                description=f"{member_name} - {ticker} (Source: {source})",
                ticker=ticker,
                amount_min=amount_min,
                amount_max=amount_max,
                owner="Self",
            )
            db.add(transaction)
            return "imported"

        except Exception as e:
            logger.error(f"Error in _ingest_trade: {e}")
            return "error"

    def _create_member_from_trade(self, db: Session, trade_data: Dict[str, Any]) -> Member | None:
        """Auto-create a member from trade data if they don't exist.

        Only called when we have a stable ``BioGuideID``. The previous
        version assigned raw strings to the ``party`` and ``chamber``
        columns, which silently coerced under SQLite but breaks under
        Postgres' strict enum types. Now we map to the proper enums and
        skip cleanly when the values aren't recognized.
        """
        try:
            bioguide_id = trade_data.get("BioGuideID")
            if not bioguide_id:
                # Caller should have routed name-only trades to _find_member;
                # this is a safety net.
                return None

            name = (
                trade_data.get("Name")
                or trade_data.get("Representative")
                or trade_data.get("Senator")
                or ""
            ).strip()
            parts = name.replace(",", "").strip().split()
            if len(parts) < 2:
                return None

            first_name = parts[0]
            last_name = parts[-1]

            chamber_raw = (trade_data.get("Chamber") or "").strip().lower()
            chamber = Chamber.SENATE if chamber_raw == "senate" else Chamber.HOUSE

            party_raw = (trade_data.get("Party") or "").strip().upper()
            party = _PARTY_BY_LETTER.get(party_raw, Party.OTHER)

            state = (trade_data.get("State") or "").strip().upper() or "??"

            member = Member(
                bioguide_id=bioguide_id,
                first_name=first_name,
                last_name=last_name,
                party=party,
                state=state,
                chamber=chamber,
                in_office=False,  # Conservative default; refresh from /api/members later.
            )
            db.add(member)
            db.flush()
            logger.info(
                "Auto-created member: %s %s (%s, %s, %s)",
                first_name,
                last_name,
                bioguide_id,
                party.value,
                chamber.value,
            )
            return member

        except Exception:
            logger.exception("Failed to auto-create member from trade")
            return None

    def _find_member(self, db: Session, name: str) -> Member | None:
        """Find member by name."""
        # Try exact match
        member = (
            db.query(Member)
            .filter(
                (Member.first_name + " " + Member.last_name).ilike(name)
                | (Member.last_name + ", " + Member.first_name).ilike(name)
            )
            .first()
        )

        if member:
            return member

        # Try partial match
        parts = name.replace(",", "").strip().split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            member = (
                db.query(Member)
                .filter(
                    Member.first_name.ilike(f"{first}%"),
                    Member.last_name.ilike(f"{last}%"),
                )
                .first()
            )

        return member

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse date string in various formats."""
        if not date_str:
            return None

        formats = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"]
        for fmt in formats:
            try:
                return datetime.strptime(str(date_str).strip(), fmt)
            except (ValueError, TypeError):
                continue
        return None


def ingest_quiverquant_trades(
    db: Session,
    chamber: str = "both",
    mode: str = "live",
) -> Dict[str, int]:
    """Ingest QuiverQuant trades into the database.

    ``mode``:
        ``live`` (default) — `/live/congresstrading`, recent trades only.
            Use this for the daily cron and any incremental sync. The
            bulk endpoint returns the *entire* history (~MB of duplicates
            on every call) which is wasteful for routine refreshes.
        ``bulk`` — `/bulk/congresstrading`, full history. Use only for
            initial seed or an explicit "regenerate from scratch" admin
            action.
        ``historical`` — per-chamber historical endpoints; rarely needed.
    """
    try:
        client = QuiverQuantClient()
        return client.ingest_trades(db, chamber, mode=mode)
    except ValueError as e:
        logger.error("QuiverQuant configuration error: %s", e)
        return {"imported": 0, "duplicates": 0, "errors": 0}
