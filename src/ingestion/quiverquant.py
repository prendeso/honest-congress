"""QuiverQuant API client for congressional trading data."""
import logging
import os
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from src.db.models import Disclosure, Transaction, Member, TransactionType
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


class QuiverQuantClient:
    """Client for QuiverQuant congressional trading API."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize QuiverQuant client."""
        self.api_key = api_key or os.getenv("QUIVERQUANT_API_KEY")

        if not self.api_key:
            raise ValueError(
                "QuiverQuant API key not provided. "
                "Set QUIVERQUANT_API_KEY environment variable or pass api_key parameter."
            )

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HonestCongress/1.0",
            "Authorization": f"Bearer {self.api_key}",
        })
        self.delay = 0.5  # Rate limiting

    # =========================================================================
    # BULK ENDPOINT - Recommended for complete coverage
    # =========================================================================

    def get_bulk_congress_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch ALL congressional trades (House + Senate, active + retired members).
        This is the recommended endpoint for comprehensive historical data.
        """
        return self._fetch_trades(BULK_CONGRESS_ENDPOINT, limit)

    # =========================================================================
    # HISTORICAL ENDPOINTS - Full historical data by chamber
    # =========================================================================

    def get_historical_house_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch historical House trading data (all members)."""
        return self._fetch_trades(HISTORICAL_HOUSE_ENDPOINT, limit)

    def get_historical_senate_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch historical Senate trading data (all members)."""
        return self._fetch_trades(HISTORICAL_SENATE_ENDPOINT, limit)

    def get_historical_congress_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch historical Congress trading data (House + Senate)."""
        return self._fetch_trades(HISTORICAL_CONGRESS_ENDPOINT, limit)

    # =========================================================================
    # LIVE ENDPOINTS - Recent data only
    # =========================================================================

    def get_live_house_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch recent House trades."""
        return self._fetch_trades(LIVE_HOUSE_ENDPOINT, limit)

    def get_live_senate_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch recent Senate trades."""
        return self._fetch_trades(LIVE_SENATE_ENDPOINT, limit)

    def get_live_congress_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch recent Congress trades."""
        return self._fetch_trades(LIVE_CONGRESS_ENDPOINT, limit)

    # Legacy aliases
    def get_house_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.get_historical_house_trades(limit)

    def get_senate_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.get_historical_senate_trades(limit)

    # =========================================================================
    # INTERNAL METHODS
    # =========================================================================

    def _fetch_trades(self, endpoint: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """Fetch trades from an endpoint."""
        try:
            logger.info(f"Fetching trades from {endpoint}")
            response = self.session.get(endpoint, timeout=60)
            response.raise_for_status()

            data = response.json()
            if not isinstance(data, list):
                logger.warning(f"Unexpected response format: {type(data)}")
                return []

            trades = data[:limit] if limit else data
            logger.info(f"Fetched {len(trades)} trades")
            return trades

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Invalid API key - got 401 Unauthorized")
            elif e.response.status_code == 429:
                logger.error("Rate limited - got 429 Too Many Requests")
            else:
                logger.error(f"HTTP error {e.response.status_code}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
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
                trade_data.get("Name") or
                trade_data.get("Representative") or
                trade_data.get("Senator")
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

            # Find member by BioGuideID or name
            bioguide_id = trade_data.get("BioGuideID")
            member = None

            if bioguide_id:
                member = db.query(Member).filter(Member.bioguide_id == bioguide_id).first()

            if not member:
                member = self._find_member(db, member_name)

            if not member:
                # Auto-create member from trade data if not found
                member = self._create_member_from_trade(db, trade_data)
                if not member:
                    return "error"

            # Check for duplicates
            existing = db.query(Transaction).filter(
                Transaction.ticker == ticker,
                Transaction.transaction_type == txn_type,
                Transaction.transaction_date == transaction_date,
            ).filter(
                Transaction.disclosure.has(Disclosure.member_id == member.id)
            ).first()

            if existing:
                return "duplicate"

            # Create or find disclosure record
            disclosure = db.query(Disclosure).filter(
                Disclosure.member_id == member.id,
                Disclosure.filing_year == safe_transaction_date.year,
                Disclosure.document_id.like(f"QANT_{member.id}_%"),
            ).first()

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

            # Parse amount
            amount_min = None
            amount_max = None
            if amount_str:
                try:
                    amount_val = float(amount_str)
                    amount_min = amount_val
                    amount_max = amount_val
                except (ValueError, TypeError):
                    pass

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

    def _create_member_from_trade(self, db: Session, trade_data: Dict[str, Any]) -> Optional[Member]:
        """Auto-create a member from trade data if they don't exist."""
        try:
            name = (
                trade_data.get("Name") or
                trade_data.get("Representative") or
                trade_data.get("Senator")
            )
            if not name:
                return None

            name = name.strip()
            parts = name.replace(",", "").strip().split()
            if len(parts) < 2:
                return None

            first_name = parts[0]
            last_name = parts[-1]

            # Get other fields from trade data
            bioguide_id = trade_data.get("BioGuideID")
            party = trade_data.get("Party", "")
            state = trade_data.get("State", "")
            chamber = trade_data.get("Chamber", "")

            # Determine chamber type
            if chamber.lower() == "senate":
                chamber_type = "senate"
            else:
                chamber_type = "house"

            member = Member(
                bioguide_id=bioguide_id,
                first_name=first_name,
                last_name=last_name,
                party=party.upper() if party else None,
                state=state.upper() if state else None,
                chamber=chamber_type,
                in_office=False,  # Assume not in office, can be updated later
            )
            db.add(member)
            db.flush()
            logger.info(f"Auto-created member: {first_name} {last_name} ({bioguide_id})")
            return member

        except Exception as e:
            logger.error(f"Error creating member: {e}")
            return None

    def _find_member(self, db: Session, name: str) -> Optional[Member]:
        """Find member by name."""
        # Try exact match
        member = db.query(Member).filter(
            (Member.first_name + ' ' + Member.last_name).ilike(name) |
            (Member.last_name + ', ' + Member.first_name).ilike(name)
        ).first()

        if member:
            return member

        # Try partial match
        parts = name.replace(",", "").strip().split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            member = db.query(Member).filter(
                Member.first_name.ilike(f"{first}%"),
                Member.last_name.ilike(f"{last}%"),
            ).first()

        return member

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
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


def ingest_quiverquant_trades(db: Session, chamber: str = "both") -> Dict[str, int]:
    """Convenience function to ingest QuiverQuant trades using bulk endpoint."""
    try:
        client = QuiverQuantClient()
        return client.ingest_trades(db, chamber, mode="bulk")
    except ValueError as e:
        logger.error(f"QuiverQuant configuration error: {e}")
        return {"imported": 0, "duplicates": 0, "errors": 0}

