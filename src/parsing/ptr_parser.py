"""Specialized parser for Periodic Transaction Reports (PTRs)."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# PTR-specific value ranges (often different from annual disclosures)
PTR_VALUE_RANGES = {
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

# Transaction type keywords
BUY_KEYWORDS = ["purchase", "buy", "bought", "p"]
SELL_KEYWORDS = ["sale", "sell", "sold", "s"]
EXCHANGE_KEYWORDS = ["exchange", "ex"]

# Common ticker pattern
TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")

# Words that look like tickers but aren't
NON_TICKERS = {
    "THE",
    "AND",
    "INC",
    "LLC",
    "LP",
    "NA",
    "CO",
    "US",
    "USA",
    "ETF",
    "SP",
    "JT",
    "DC",
    "NY",
    "CA",
    "TX",
    "FL",
    "IL",
    "PA",
    "OH",
    "PTR",
    "PDF",
    "FD",
    "REP",
    "SEN",
    "HON",
}


class PTRParser:
    """Parser specifically designed for PTR (Periodic Transaction Report) PDFs."""

    def __init__(self):
        self.current_year = datetime.now().year

    def parse_ptr(self, pdf_path: str) -> Dict[str, Any]:
        """
        Parse a PTR PDF file.

        PTRs have a simpler structure than annual disclosures:
        - Asset description
        - Transaction type (Purchase/Sale)
        - Transaction date
        - Amount range
        - Owner (Self, Spouse, Joint, Dependent Child)

        Args:
            pdf_path: Path to the PTR PDF file

        Returns:
            Dict with transactions and any parse errors
        """
        result = {
            "transactions": [],
            "filer_info": {},
            "filing_date": None,
            "parse_errors": [],
        }

        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_text = ""
                all_tables = []

                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    all_text += page_text + "\n"

                    page_tables = page.extract_tables()
                    if page_tables:
                        all_tables.extend(page_tables)

                # Extract filer information from header
                result["filer_info"] = self._extract_filer_info(all_text)
                result["filing_date"] = self._extract_filing_date(all_text)

                # Try to parse from tables first (most reliable)
                if all_tables:
                    result["transactions"] = self._parse_tables(all_tables)

                # Fall back to text parsing if no tables found
                if not result["transactions"]:
                    result["transactions"] = self._parse_text(all_text)

                logger.info(f"Parsed {len(result['transactions'])} transactions from PTR")

        except Exception as e:
            logger.error(f"Error parsing PTR {pdf_path}: {e}")
            result["parse_errors"].append(str(e))

        return result

    def _extract_filer_info(self, text: str) -> Dict[str, str]:
        """Extract filer name, state, district from PTR header."""
        info = {}

        # Look for name pattern - usually first line or after "Name:"
        name_match = re.search(r"(?:Name:\s*)?([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)", text)
        if name_match:
            info["name"] = name_match.group(1)

        # Look for state/district
        state_match = re.search(r"\b([A-Z]{2})\s*-?\s*(\d{1,2})\b", text)
        if state_match:
            info["state"] = state_match.group(1)
            info["district"] = state_match.group(2)

        return info

    def _extract_filing_date(self, text: str) -> datetime | None:
        """Extract filing date from PTR."""
        # Look for "Filed:" or "Filing Date:" patterns
        date_match = re.search(
            r"(?:Filed|Filing\s*Date):\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE
        )
        if date_match:
            return self._parse_date(date_match.group(1))
        return None

    def _parse_tables(self, tables: List[List[List[str]]]) -> List[Dict[str, Any]]:
        """Parse transactions from extracted tables."""
        transactions = []

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Check if this looks like a transaction table
            headers = table[0] if table else []
            header_text = " ".join(str(h).lower() for h in headers if h)

            # PTR tables typically have: Asset, Transaction, Date, Amount, Owner
            is_transaction_table = any(
                kw in header_text
                for kw in ["transaction", "asset", "purchase", "sale", "amount", "date"]
            )

            if not is_transaction_table:
                continue

            # Determine column indices
            col_indices = self._identify_columns(headers)

            # Parse each data row
            for row in table[1:]:
                txn = self._parse_table_row(row, col_indices)
                if txn:
                    transactions.append(txn)

        return transactions

    def _identify_columns(self, headers: List[str]) -> Dict[str, int]:
        """Identify which column contains which data."""
        indices = {
            "asset": 0,
            "type": 1,
            "date": 2,
            "amount": 3,
            "owner": 4,
        }

        for i, header in enumerate(headers):
            if not header:
                continue
            h = str(header).lower()

            if "asset" in h or "description" in h or "name" in h:
                indices["asset"] = i
            elif "type" in h or "transaction" in h:
                indices["type"] = i
            elif "date" in h:
                indices["date"] = i
            elif "amount" in h or "value" in h:
                indices["amount"] = i
            elif "owner" in h or "filer" in h:
                indices["owner"] = i

        return indices

    def _parse_table_row(
        self, row: List[Any], col_indices: Dict[str, int]
    ) -> Dict[str, Any] | None:
        """Parse a single transaction row."""
        if not row:
            return None

        # Clean row values
        row = [str(cell).strip() if cell else "" for cell in row]

        # Extract values based on column indices
        def get_col(name: str) -> str:
            idx = col_indices.get(name, -1)
            return row[idx] if 0 <= idx < len(row) else ""

        description = get_col("asset")
        txn_type_raw = get_col("type")
        date_raw = get_col("date")
        amount_raw = get_col("amount")
        owner = get_col("owner")

        # Skip empty rows
        if not description and not txn_type_raw:
            return None

        # Parse transaction type
        txn_type = self._parse_transaction_type(txn_type_raw)
        if not txn_type:
            # Try to infer from description
            txn_type = self._infer_transaction_type(description)

        # Parse date
        txn_date = self._parse_date(date_raw)

        # Parse amount range
        amount_min, amount_max = self._parse_amount_range(amount_raw)

        # Extract ticker
        ticker = self._extract_ticker(description)

        # Determine asset type
        asset_type = self._determine_asset_type(description)

        return {
            "description": description,
            "ticker": ticker,
            "asset_type": asset_type,
            "transaction_type": txn_type,
            "transaction_date": txn_date,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "owner": self._normalize_owner(owner),
        }

    def _parse_text(self, text: str) -> List[Dict[str, Any]]:
        """Parse transactions from plain text (fallback method)."""
        transactions = []

        # Look for transaction patterns in text
        # Common pattern: "ASSET DESCRIPTION    P/S    MM/DD/YYYY    $X - $Y    Owner"
        lines = text.split("\n")

        for line in lines:
            # Skip header/footer lines
            if any(
                skip in line.lower()
                for skip in [
                    "transaction",
                    "asset",
                    "owner",
                    "amount",
                    "date",
                    "periodic",
                    "report",
                    "page",
                    "filing",
                ]
            ):
                continue

            # Try to match transaction pattern
            txn = self._parse_text_line(line)
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_text_line(self, line: str) -> Dict[str, Any] | None:
        """Try to parse a single line as a transaction."""
        if not line or len(line) < 20:
            return None

        # Look for key indicators
        has_dollar = "$" in line
        has_date = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line)

        if not (has_dollar or has_date):
            return None

        # Try to extract components
        date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", line)
        amount_match = re.search(r"\$[\d,]+\s*-\s*\$[\d,]+", line)

        # Extract transaction type
        txn_type = None
        for kw in BUY_KEYWORDS:
            if re.search(rf"\b{kw}\b", line, re.IGNORECASE):
                txn_type = "purchase"
                break
        if not txn_type:
            for kw in SELL_KEYWORDS:
                if re.search(rf"\b{kw}\b", line, re.IGNORECASE):
                    txn_type = "sale"
                    break

        if not txn_type:
            return None

        # Extract description (everything before date or amount)
        description = line
        if date_match:
            description = line[: date_match.start()].strip()
        elif amount_match:
            description = line[: amount_match.start()].strip()

        # Clean up description
        for kw in BUY_KEYWORDS + SELL_KEYWORDS:
            description = re.sub(rf"\b{kw}\b", "", description, flags=re.IGNORECASE)
        description = re.sub(r"\s+", " ", description).strip()

        if not description:
            return None

        return {
            "description": description,
            "ticker": self._extract_ticker(description),
            "asset_type": self._determine_asset_type(description),
            "transaction_type": txn_type,
            "transaction_date": self._parse_date(date_match.group(1)) if date_match else None,
            "amount_min": self._parse_amount_range(amount_match.group(0))[0]
            if amount_match
            else None,
            "amount_max": self._parse_amount_range(amount_match.group(0))[1]
            if amount_match
            else None,
            "owner": "Self",
        }

    def _parse_transaction_type(self, text: str) -> str | None:
        """Parse transaction type from text."""
        if not text:
            return None

        text_lower = text.lower().strip()

        for kw in BUY_KEYWORDS:
            if kw in text_lower or text_lower == kw[0]:
                return "purchase"

        for kw in SELL_KEYWORDS:
            if kw in text_lower or text_lower == kw[0]:
                return "sale"

        for kw in EXCHANGE_KEYWORDS:
            if kw in text_lower:
                return "exchange"

        return None

    def _infer_transaction_type(self, description: str) -> str | None:
        """Try to infer transaction type from description."""
        desc_lower = description.lower()

        if "purchase" in desc_lower or "bought" in desc_lower:
            return "purchase"
        elif "sale" in desc_lower or "sold" in desc_lower:
            return "sale"

        return None

    def _parse_date(self, text: str) -> datetime | None:
        """Parse date from text."""
        if not text:
            return None

        text = text.strip()

        formats = [
            "%m/%d/%Y",
            "%m/%d/%y",
            "%Y-%m-%d",
            "%m-%d-%Y",
            "%m-%d-%y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        return None

    def _parse_amount_range(self, text: str) -> Tuple[Decimal | None, Decimal | None]:
        """Parse amount range from text."""
        if not text:
            return None, None

        # Check against known ranges
        for range_text, (min_val, max_val) in PTR_VALUE_RANGES.items():
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

    def _extract_ticker(self, text: str) -> str | None:
        """Extract stock ticker from text."""
        if not text:
            return None

        # Look for explicit ticker notation like (AAPL) or [MSFT]
        explicit = re.search(r"[\(\[]([A-Z]{1,5})[\)\]]", text)
        if explicit:
            ticker = explicit.group(1)
            if ticker not in NON_TICKERS:
                return ticker

        # Look for ticker at start of description (common PTR format)
        start_match = re.match(r"^([A-Z]{1,5})\s*[-–—]\s", text)
        if start_match:
            ticker = start_match.group(1)
            if ticker not in NON_TICKERS:
                return ticker

        # Look for standalone tickers near stock keywords
        for keyword in ["stock", "common", "shares"]:
            if keyword in text.lower():
                tickers = TICKER_PATTERN.findall(text)
                for t in tickers:
                    if t not in NON_TICKERS and len(t) >= 2:
                        return t

        return None

    def _determine_asset_type(self, description: str) -> str:
        """Determine asset type from description."""
        desc_lower = description.lower()

        if any(kw in desc_lower for kw in ["stock", "shares", "common", "equity"]):
            return "stock"
        elif any(kw in desc_lower for kw in ["option", "call", "put"]):
            return "option"
        elif any(kw in desc_lower for kw in ["bond", "treasury", "note", "fixed income"]):
            return "bond"
        elif any(kw in desc_lower for kw in ["mutual fund", "index fund", "etf", "fund"]):
            return "mutual_fund"
        elif any(kw in desc_lower for kw in ["crypto", "bitcoin", "ethereum"]):
            return "cryptocurrency"
        else:
            return "other"

    def _normalize_owner(self, owner: str) -> str:
        """Normalize owner field."""
        if not owner:
            return "Self"

        owner_lower = owner.lower().strip()

        if "spouse" in owner_lower or owner_lower == "sp":
            return "Spouse"
        elif "joint" in owner_lower or owner_lower == "jt":
            return "Joint"
        elif "child" in owner_lower or "dependent" in owner_lower or owner_lower == "dc":
            return "Dependent Child"
        else:
            return "Self"


def parse_ptr(pdf_path: str) -> Dict[str, Any]:
    """
    Convenience function to parse a PTR PDF.

    Args:
        pdf_path: Path to the PTR PDF file

    Returns:
        Parsed transaction data
    """
    parser = PTRParser()
    return parser.parse_ptr(pdf_path)
