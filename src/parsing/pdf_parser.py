"""PDF parsing for financial disclosures."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# Value ranges used in disclosures
VALUE_RANGES = {
    "$1 - $1,000": (1, 1000),
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}

# Common ticker patterns
TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")
STOCK_KEYWORDS = ["common stock", "stock", "shares", "equity"]


class DisclosureParser:
    """Parser for congressional financial disclosure PDFs."""

    def __init__(self):
        self.current_year = datetime.now().year

    def parse_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """
        Parse a financial disclosure PDF.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Parsed disclosure data including assets, transactions, and liabilities
        """
        result = {
            "assets": [],
            "transactions": [],
            "liabilities": [],
            "earned_income": [],
            "positions": [],
            "agreements": [],
            "parse_errors": [],
        }

        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = ""
                tables = []

                for page in pdf.pages:
                    # Extract text
                    page_text = page.extract_text() or ""
                    text += page_text + "\n"

                    # Extract tables
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)

                # Identify document sections and parse accordingly
                result["assets"] = self._parse_assets_section(text, tables)
                result["transactions"] = self._parse_transactions_section(text, tables)
                result["liabilities"] = self._parse_liabilities_section(text, tables)
                result["earned_income"] = self._parse_income_section(text, tables)

        except Exception as e:
            logger.error(f"Error parsing PDF {pdf_path}: {e}")
            result["parse_errors"].append(str(e))

        return result

    def _parse_assets_section(
        self, text: str, tables: List[List[List[str]]]
    ) -> List[Dict[str, Any]]:
        """Parse the assets/Schedule A section."""
        assets = []

        # Look for asset tables
        for table in tables:
            if not table:
                continue

            # Check if this looks like an asset table
            headers = table[0] if table else []
            header_text = " ".join(str(h).lower() for h in headers if h)

            if "asset" in header_text or "value" in header_text:
                # Parse each row as an asset
                for row in table[1:]:
                    asset = self._parse_asset_row(row)
                    if asset:
                        assets.append(asset)

        # Also try to extract assets from text using patterns
        text_assets = self._extract_assets_from_text(text)
        assets.extend(text_assets)

        return assets

    def _parse_asset_row(self, row: List[Any]) -> Dict[str, Any] | None:
        """Parse a single asset row from a table."""
        if not row or len(row) < 2:
            return None

        # Clean up row values
        row = [str(cell).strip() if cell else "" for cell in row]

        # Try to identify description and value columns
        description = row[0] if row else ""
        value_text = ""
        income_text = ""

        for cell in row[1:]:
            if self._looks_like_value_range(cell):
                if not value_text:
                    value_text = cell
                else:
                    income_text = cell

        if not description:
            return None

        # Extract ticker if present
        ticker = self._extract_ticker(description)

        # Determine asset type
        asset_type = self._determine_asset_type(description)

        # Parse value range
        value_min, value_max = self._parse_value_range(value_text)
        income_min, income_max = self._parse_value_range(income_text)

        return {
            "description": description,
            "ticker": ticker,
            "asset_type": asset_type,
            "value_min": value_min,
            "value_max": value_max,
            "income_min": income_min,
            "income_max": income_max,
        }

    def _parse_transactions_section(
        self, text: str, tables: List[List[List[str]]]
    ) -> List[Dict[str, Any]]:
        """Parse the transactions/Schedule B section (PTR)."""
        transactions = []

        for table in tables:
            if not table:
                continue

            headers = table[0] if table else []
            header_text = " ".join(str(h).lower() for h in headers if h)

            if "transaction" in header_text or "purchase" in header_text or "sale" in header_text:
                for row in table[1:]:
                    txn = self._parse_transaction_row(row)
                    if txn:
                        transactions.append(txn)

        return transactions

    def _parse_transaction_row(self, row: List[Any]) -> Dict[str, Any] | None:
        """Parse a single transaction row."""
        if not row or len(row) < 3:
            return None

        row = [str(cell).strip() if cell else "" for cell in row]

        # Common PTR format: Asset, Transaction Type, Date, Amount, Owner
        description = row[0] if len(row) > 0 else ""
        txn_type = row[1] if len(row) > 1 else ""
        date_text = row[2] if len(row) > 2 else ""
        amount_text = row[3] if len(row) > 3 else ""
        owner = row[4] if len(row) > 4 else ""

        # Parse transaction type
        txn_type_normalized = self._normalize_transaction_type(txn_type)
        if not txn_type_normalized:
            return None

        # Parse date
        txn_date = self._parse_date(date_text)

        # Parse amount
        amount_min, amount_max = self._parse_value_range(amount_text)

        # Extract ticker
        ticker = self._extract_ticker(description)

        return {
            "description": description,
            "ticker": ticker,
            "transaction_type": txn_type_normalized,
            "transaction_date": txn_date,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "owner": owner,
        }

    def _parse_liabilities_section(
        self, text: str, tables: List[List[List[str]]]
    ) -> List[Dict[str, Any]]:
        """Parse the liabilities section."""
        liabilities = []

        for table in tables:
            if not table:
                continue

            headers = table[0] if table else []
            header_text = " ".join(str(h).lower() for h in headers if h)

            if "liabilit" in header_text or "creditor" in header_text:
                for row in table[1:]:
                    liability = self._parse_liability_row(row)
                    if liability:
                        liabilities.append(liability)

        return liabilities

    def _parse_liability_row(self, row: List[Any]) -> Dict[str, Any] | None:
        """Parse a single liability row."""
        if not row or len(row) < 2:
            return None

        row = [str(cell).strip() if cell else "" for cell in row]

        creditor = row[0] if row else ""
        amount_text = ""
        description = ""

        for cell in row[1:]:
            if self._looks_like_value_range(cell):
                amount_text = cell
            elif cell and not description:
                description = cell

        if not creditor:
            return None

        amount_min, amount_max = self._parse_value_range(amount_text)

        return {
            "creditor": creditor,
            "description": description,
            "amount_min": amount_min,
            "amount_max": amount_max,
        }

    def _parse_income_section(
        self, text: str, tables: List[List[List[str]]]
    ) -> List[Dict[str, Any]]:
        """Parse earned income section."""
        income = []

        for table in tables:
            if not table:
                continue

            headers = table[0] if table else []
            header_text = " ".join(str(h).lower() for h in headers if h)

            if "income" in header_text and "earned" in header_text:
                for row in table[1:]:
                    if len(row) >= 2:
                        source = str(row[0]).strip() if row[0] else ""
                        amount = str(row[1]).strip() if len(row) > 1 and row[1] else ""

                        if source:
                            income.append(
                                {
                                    "source": source,
                                    "amount": amount,
                                }
                            )

        return income

    def _extract_assets_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Extract assets from plain text (fallback for poorly formatted PDFs)."""
        assets = []

        # Pattern: Stock description followed by value range
        pattern = r"([A-Z][A-Za-z\s\.\,\&]+(?:stock|fund|bond|account))\s*[\-\:]\s*(\$[\d\,]+\s*-\s*\$[\d\,]+)"

        matches = re.findall(pattern, text, re.IGNORECASE)
        for desc, value in matches:
            value_min, value_max = self._parse_value_range(value)
            ticker = self._extract_ticker(desc)

            assets.append(
                {
                    "description": desc.strip(),
                    "ticker": ticker,
                    "asset_type": self._determine_asset_type(desc),
                    "value_min": value_min,
                    "value_max": value_max,
                }
            )

        return assets

    def _extract_ticker(self, text: str) -> str | None:
        """Extract stock ticker from text."""
        # Look for explicit ticker notation like (AAPL) or [MSFT]
        explicit = re.search(r"[\(\[]([A-Z]{1,5})[\)\]]", text)
        if explicit:
            return explicit.group(1)

        # Look for common patterns like "Apple Inc (AAPL)"
        # or just standalone ticker with stock keywords
        for keyword in STOCK_KEYWORDS:
            if keyword in text.lower():
                tickers = TICKER_PATTERN.findall(text)
                # Filter out common non-ticker words
                non_tickers = {"THE", "AND", "INC", "LLC", "LP", "NA", "CO", "US", "USA"}
                tickers = [t for t in tickers if t not in non_tickers]
                if tickers:
                    return tickers[0]

        return None

    def _determine_asset_type(self, description: str) -> str:
        """Determine asset type from description."""
        desc_lower = description.lower()

        if any(kw in desc_lower for kw in ["stock", "shares", "equity"]):
            return "stock"
        elif any(kw in desc_lower for kw in ["bond", "treasury", "note"]):
            return "bond"
        elif any(kw in desc_lower for kw in ["mutual fund", "index fund", "etf"]):
            return "mutual_fund"
        elif any(kw in desc_lower for kw in ["real estate", "property", "land"]):
            return "real_estate"
        elif any(kw in desc_lower for kw in ["401k", "ira", "retirement", "pension"]):
            return "retirement"
        elif any(kw in desc_lower for kw in ["bank", "checking", "savings", "cd"]):
            return "bank_account"
        else:
            return "other"

    def _looks_like_value_range(self, text: str) -> bool:
        """Check if text looks like a value range."""
        if not text:
            return False
        return bool(re.search(r"\$[\d,]+", text))

    def _parse_value_range(self, text: str) -> Tuple[Decimal | None, Decimal | None]:
        """Parse a value range string into min/max decimals."""
        if not text:
            return None, None

        # Check against known ranges first
        for range_text, (min_val, max_val) in VALUE_RANGES.items():
            if range_text.lower() in text.lower():
                return (
                    Decimal(min_val) if min_val else None,
                    Decimal(max_val) if max_val else None,
                )

        # Try to parse custom range
        amounts = re.findall(r"\$?([\d,]+)", text)
        amounts = [int(a.replace(",", "")) for a in amounts if a]

        if len(amounts) >= 2:
            return Decimal(min(amounts)), Decimal(max(amounts))
        elif len(amounts) == 1:
            return Decimal(amounts[0]), Decimal(amounts[0])

        return None, None

    def _normalize_transaction_type(self, text: str) -> str | None:
        """Normalize transaction type text."""
        if not text:
            return None

        text_lower = text.lower()

        if "purchase" in text_lower or "buy" in text_lower:
            return "purchase"
        elif "sale" in text_lower or "sell" in text_lower or "sold" in text_lower:
            return "sale"
        elif "exchange" in text_lower:
            return "exchange"

        return None

    def _parse_date(self, text: str) -> datetime | None:
        """Parse date from various formats."""
        if not text:
            return None

        # Common date formats in disclosures
        formats = [
            "%m/%d/%Y",
            "%m/%d/%y",
            "%Y-%m-%d",
            "%B %d, %Y",
            "%b %d, %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue

        return None


def parse_disclosure(pdf_path: str) -> Dict[str, Any]:
    """
    Convenience function to parse a disclosure PDF.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Parsed disclosure data
    """
    parser = DisclosureParser()
    return parser.parse_pdf(pdf_path)
