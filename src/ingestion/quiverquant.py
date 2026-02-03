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

# QuiverQuant API endpoints
QUIVERQUANT_BASE = "https://api.quiverquant.com/beta"
HOUSE_ENDPOINT = f"{QUIVERQUANT_BASE}/live/housetrading"
SENATE_ENDPOINT = f"{QUIVERQUANT_BASE}/live/senatetrading"


class QuiverQuantClient:
    """Client for QuiverQuant congressional trading API."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize QuiverQuant client.

        Args:
            api_key: QuiverQuant API key. If None, reads from QUIVERQUANT_API_KEY env var.

        Raises:
            ValueError: If no API key provided or found in environment.
        """
        self.api_key = api_key or os.getenv("QUIVERQUANT_API_KEY")

        if not self.api_key:
            raise ValueError(
                "QuiverQuant API key not provided. "
                "Set QUIVERQUANT_API_KEY environment variable or pass api_key parameter."
            )

        self.session = requests.Session()
        # Use Bearer token authentication (correct for Tier 1 API)
        self.session.headers.update({
            "User-Agent": "HonestCongress/1.0",
            "Authorization": f"Bearer {self.api_key}",
        })
        self.delay = 0.5  # Rate limiting

    def get_house_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch House stock trades.

        Args:
            limit: Maximum number of trades to fetch

        Returns:
            List of trade records
        """
        return self._fetch_trades(HOUSE_ENDPOINT, limit)

    def get_senate_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch Senate stock trades.

        Args:
            limit: Maximum number of trades to fetch

        Returns:
            List of trade records
        """
        return self._fetch_trades(SENATE_ENDPOINT, limit)

    def _fetch_trades(self, endpoint: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """
        Fetch trades from an endpoint.

        Args:
            endpoint: API endpoint URL
            limit: Maximum trades to fetch

        Returns:
            List of trade records
        """
        try:
            logger.info(f"Fetching trades from {endpoint}")

            response = self.session.get(endpoint, timeout=30)
            response.raise_for_status()

            data = response.json()

            # API returns list of trades
            if not isinstance(data, list):
                logger.warning(f"Unexpected response format: {type(data)}")
                return []

            trades = data
            if limit:
                trades = trades[:limit]

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
    ) -> Dict[str, int]:
        """
        Ingest congressional trades into database.

        Args:
            db: Database session
            chamber: "house", "senate", or "both"
            source: Source identifier for tracking

        Returns:
            Summary of ingestion results
        """
        results = {"imported": 0, "duplicates": 0, "errors": 0}

        # Fetch trades
        all_trades = []

        if chamber in ["house", "both"]:
            house_trades = self.get_house_trades()
            all_trades.extend(house_trades)
            time.sleep(self.delay)

        if chamber in ["senate", "both"]:
            senate_trades = self.get_senate_trades()
            all_trades.extend(senate_trades)

        logger.info(f"Processing {len(all_trades)} trades for ingestion")

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

    def _ingest_trade(
        self,
        db: Session,
        trade_data: Dict[str, Any],
        source: str,
    ) -> str:
        """
        Ingest a single trade record.

        Args:
            db: Database session
            trade_data: Trade data from API
            source: Source identifier

        Returns:
            "imported", "duplicate", or "error"
        """
        try:
            # Extract member name (House uses "Representative", Senate uses "Senator")
            member_name = trade_data.get("Representative") or trade_data.get("Senator")
            if not member_name:
                logger.debug(f"No member name found in: {trade_data}")
                return "error"

            member_name = member_name.strip()

            # Extract other fields
            ticker = trade_data.get("Ticker", "").upper().strip()
            transaction_str = trade_data.get("Transaction", "").lower()
            amount_str = trade_data.get("Amount", "")
            date_str = trade_data.get("Date")
            last_modified_str = trade_data.get("last_modified")

            # Validate required fields
            if not all([member_name, ticker, transaction_str, date_str]):
                logger.debug(f"Incomplete trade data: {trade_data}")
                return "error"

            # Map transaction type
            if "purchase" in transaction_str or "buy" in transaction_str:
                txn_type = TransactionType.PURCHASE
            elif "sale" in transaction_str or "sell" in transaction_str:
                txn_type = TransactionType.SALE
            elif "exchange" in transaction_str:
                txn_type = TransactionType.EXCHANGE
            else:
                logger.debug(f"Unknown transaction type: {transaction_str}")
                return "error"

            # Parse dates
            transaction_date = self._parse_date(date_str)
            filed_date = self._parse_date(last_modified_str or date_str)

            if not transaction_date:
                logger.debug(f"Could not parse date: {date_str}")
                return "error"

            safe_transaction_date = choose_transaction_date(transaction_date, filed_date)
            safe_filed_date = choose_filing_date(filed_date, safe_transaction_date.year)

            # Find member by BioGuideID or name
            bioguide_id = trade_data.get("BioGuideID")
            member = None

            if bioguide_id:
                member = db.query(Member).filter(Member.bioguide_id == bioguide_id).first()

            if not member:
                # Try by name
                member = self._find_member(db, member_name)

            if not member:
                logger.debug(f"Could not find member: {member_name} (BioGuideID: {bioguide_id})")
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
                logger.debug(f"Duplicate trade found: {ticker} {txn_type} {transaction_date}")
                return "duplicate"

            # Create or find disclosure record for API source
            disclosure = db.query(Disclosure).filter(
                Disclosure.member_id == member.id,
                Disclosure.filing_year == safe_transaction_date.year,
                Disclosure.document_id.like(f"QANT_{member.id}_%"),
            ).first()

            if not disclosure:
                disclosure = Disclosure(
                    member_id=member.id,
                    filing_year=safe_transaction_date.year,
                    filing_type="T",  # Trade
                    filing_date=safe_filed_date,
                    document_id=f"QANT_{member.id}_{safe_transaction_date.year}",
                    document_url="",  # No URL for API data
                    is_ptr=True,  # QuiverQuant API data IS PTR (stock trades)
                    parsed=True,
                )
                db.add(disclosure)
                db.flush()

            # Parse amount (numeric string)
            amount_min = None
            amount_max = None

            if amount_str:
                try:
                    amount_val = float(amount_str)
                    amount_min = amount_val
                    amount_max = amount_val
                except (ValueError, TypeError):
                    pass

            # Create transaction record
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
            logger.debug(f"Imported: {member_name} {ticker} {txn_type} {transaction_date}")
            return "imported"

        except Exception as e:
            logger.error(f"Error ingesting trade: {e}")
            return "error"

    def _find_member(self, db: Session, name: str) -> Optional[Member]:
        """
        Find member by name.

        Args:
            db: Database session
            name: Member name

        Returns:
            Member object or None
        """
        # Try exact match first
        member = db.query(Member).filter(
            (Member.first_name + ' ' + Member.last_name).ilike(name) |
            (Member.last_name + ', ' + Member.first_name).ilike(name)
        ).first()

        if member:
            return member

        # Try splitting name
        parts = name.replace(",", "").strip().split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]

            member = db.query(Member).filter(
                Member.first_name.ilike(f"{first}%"),
                Member.last_name.ilike(f"{last}%"),
            ).first()

            if member:
                return member

        return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse date string in various formats.

        Args:
            date_str: Date string

        Returns:
            Datetime object or None
        """
        if not date_str:
            return None

        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m-%d-%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(str(date_str).strip(), fmt)
            except (ValueError, TypeError):
                continue

        logger.debug(f"Could not parse date: {date_str}")
        return None


def ingest_quiverquant_trades(
    db: Session,
    chamber: str = "both",
) -> Dict[str, int]:
    """
    Convenience function to ingest QuiverQuant trades.

    Args:
        db: Database session
        chamber: "house", "senate", or "both"

    Returns:
        Ingestion results
    """
    try:
        client = QuiverQuantClient()
        return client.ingest_trades(db, chamber)
    except ValueError as e:
        logger.error(f"QuiverQuant configuration error: {e}")
        return {"imported": 0, "duplicates": 0, "errors": 0}
