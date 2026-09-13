"""Specialized parser for Periodic Transaction Reports (PTRs)."""

import logging
import re
from dataclasses import asdict, dataclass
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
    # The top band on a spouse or dependent-child line: the form stops
    # itemising above $1M. Missing from this table, it fell through to the
    # generic path and came out as exactly $1,000,000 -- a precise figure the
    # filing never gave.
    "Over $1,000,000": (1000001, None),
}

# Transaction type keywords
BUY_KEYWORDS = ["purchase", "buy", "bought", "p"]
SELL_KEYWORDS = ["sale", "sell", "sold", "s"]
EXCHANGE_KEYWORDS = ["exchange", "ex"]

_DATE_PATTERN = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")

# The footnote block under each record. Its labels render with the small-caps
# glyphs as NUL bytes, so once those are stripped they read "F S:", "S O:",
# "D:", "L:" -- Filing Status, Subholding Of, Description, Location.
_FOOTNOTE_PREFIX = re.compile(r"^\s*(?:F\s+S\s*:|S\s+O\s*:|D\s*:|L\s*:)")

# Common ticker pattern
TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")

# Words that look like tickers but aren't
# House Clerk PTR filings tag each holding with a bracketed asset-class code --
# [ST] stock, [CS] common stock, [OP] options, and so on. `_extract_ticker`
# reads bracketed uppercase as a ticker, so without these a TuHURA Biosciences
# purchase tagged [CS] was recorded against a company called "CS".
ASSET_CLASS_CODES = {
    "ST",
    "CS",
    "PS",
    "OP",
    "OL",
    "OT",
    "MF",
    "ETF",
    "EF",
    "CT",
    "GS",
    "HN",
    "IH",
    "PE",
    "RP",
    "SA",
    "AB",
    "BA",
    "CO",
    "CD",
    "CR",
    "DB",
    "DO",
    "FA",
    "FN",
    "IC",
    "IR",
    "OI",
    "OO",
    "PM",
    "RE",
    "TR",
    "VI",
    "WU",
}

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


@dataclass
class ParseQuality:
    """What the parser actually managed on one document.

    Counted rather than judged: `rows_detected` includes rows that produced
    nothing, so a dropped row lowers the score by arithmetic instead of by a
    rule somebody has to remember to write.
    """

    rows_detected: int = 0
    rows_parsed: int = 0
    rows_recovered: int = 0
    text_extracted: bool = False
    tables_found: bool = False
    headers_recognised: bool = False
    used_text_fallback: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
        quality = ParseQuality()
        result: Dict[str, Any] = {
            "transactions": [],
            "filer_info": {},
            "filing_date": None,
            "parse_errors": [],
            "quality": quality.as_dict(),
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

                quality.text_extracted = bool(all_text.strip())
                quality.tables_found = bool(all_tables)

                # Try to parse from tables first (most reliable)
                if all_tables:
                    result["transactions"] = self._parse_tables(all_tables, quality)

                # Fall back to text parsing if no tables found
                if not result["transactions"]:
                    quality.used_text_fallback = True
                    text_rows = self._parse_text(all_text)
                    result["transactions"] = text_rows
                    # The text path has no notion of a candidate row, so the
                    # only honest denominator is what it produced.
                    quality.rows_detected = max(quality.rows_detected, len(text_rows))
                    quality.rows_parsed = len(text_rows)

                result["quality"] = quality.as_dict()
                logger.info(f"Parsed {len(result['transactions'])} transactions from PTR")

        except Exception as e:
            logger.error(f"Error parsing PTR {pdf_path}: {e}")
            result["parse_errors"].append(str(e))
            result["quality"] = quality.as_dict()

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

    def _parse_tables(
        self, tables: List[List[List[str]]], quality: "ParseQuality | None" = None
    ) -> List[Dict[str, Any]]:
        """Parse transactions from extracted tables."""
        quality = quality if quality is not None else ParseQuality()
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
            if self._headers_recognised(headers):
                quality.headers_recognised = True

            # Parse each data row
            for row in table[1:]:
                if not self._is_candidate_row(row):
                    # Blank spacers, and the footnote rows PTR tables interleave
                    # after each record ("Filing Status: New", "Location: ...").
                    # Those legitimately yield no transaction, so counting them
                    # as dropped would mark a clean filing as a bad one.
                    continue
                quality.rows_detected += 1
                txn = self._parse_table_row(row, col_indices)
                if txn:
                    quality.rows_parsed += 1
                    if txn.get("recovered_from_collapsed_row"):
                        quality.rows_recovered += 1
                    transactions.append(txn)

        return transactions

    @staticmethod
    def _is_candidate_row(row: List[Any]) -> bool:
        """Whether a row looks like it should yield a transaction.

        This is the denominator the confidence score divides by, so getting it
        wrong either hides dropped rows or invents them. Measured across 200
        real filings, an earlier rule -- "has a date OR a dollar sign" -- marked
        107 rows as unread transactions that were nothing of the kind:

        * the "Cap. Gains > $200?" header, which wraps onto its own rows and
          carries a dollar sign; and
        * the footnote block beneath each record ("Filing Status", "Subholding
          Of", "Description"), whose prose mentions figures like "$500,000" and
          option strike prices.

        A transaction always carries a trade date, and neither of those does, so
        the date is the requirement and an amount alone is not enough. Tightening
        it dropped 107 false candidates and exactly one "transaction": a row
        reading `["[ST]", "$50,000"]` -- an asset-class code and the wrapped half
        of an amount -- with no date, no type, and a $50,000 band invented from
        one bound.
        """
        joined = " ".join(str(cell) for cell in row if cell).replace("\x00", "")
        if not joined.strip():
            return False

        if not _DATE_PATTERN.search(joined):
            return False

        # A footnote block can still quote a date in its prose, so the date has
        # to appear on a line that is not itself a footnote.
        dated = [line for line in joined.split("\n") if _DATE_PATTERN.search(line)]
        return any(not _FOOTNOTE_PREFIX.match(line) for line in dated)

    @staticmethod
    def _headers_recognised(headers: List[str]) -> bool:
        """Whether column positions were read off the header or merely assumed.

        `_identify_columns` falls back to fixed positions 0-4 when nothing
        matches, which is a guess about a layout rather than a reading of it.
        The confidence score should not treat the two alike.
        """
        joined = " ".join(str(h).lower() for h in headers if h)
        return any(word in joined for word in ("asset", "transaction", "date", "amount", "owner"))

    def _identify_columns(self, headers: List[str]) -> Dict[str, int]:
        """Identify which column contains which data.

        House PTR tables carry TWO date columns -- "Date" (when the trade
        happened) and "Notification Date" (when the filer was told). A plain
        `"date" in header` test matches both, and since the notification column
        comes second it used to win, so almost every stored transaction_date was
        actually the notification date. Measured against 18 real 2024 filings,
        that was wrong on 29 of 31 transactions.

        It matters beyond tidiness: STOCK Act compliance is filing date minus
        transaction date, so using the notification date understates lateness by
        however long notification took.
        """
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
            # Headers wrap, so "Notification\nDate" arrives with a newline in it.
            h = " ".join(str(header).lower().split())

            # Checked before the generic "date" test so it cannot claim the slot.
            if "notification" in h:
                indices["notification_date"] = i
            elif "asset" in h or "description" in h or "name" in h:
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

        # pdfplumber sometimes fails to split a row and jams the whole record
        # into the first cell, leaving every other cell null. Read by column
        # index, that looks like an empty row and used to be dropped: across the
        # six real filings in the test corpus, 18 transactions were lost this
        # way against 16 kept, and two filings parsed to nothing at all while
        # being recorded as parsed successfully.
        populated = [cell for cell in row if cell]
        if len(populated) == 1 and row[0]:
            return self._parse_collapsed_cell(row[0])

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

        # PTR tables interleave a footnote row after each transaction --
        # "Filing Status: New", with the glyphs rendered as NUL bytes. Those
        # rows carry a non-empty description, so the emptiness check above lets
        # them through, and they used to be emitted as transactions with every
        # field None: 46% of all rows the parser produced.
        description = description.replace("\x00", "").strip()
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

        # A real transaction row always carries a date. Accepting an amount
        # instead let `["[ST]", "$50,000"]` -- an asset-class code and the
        # wrapped half of a band -- through as a transaction with a $50,000
        # amount and nothing else.
        if txn_date is None:
            return None

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
            "notification_date": self._parse_date(get_col("notification_date")),
            "amount_min": amount_min,
            "amount_max": amount_max,
            "owner": self._normalize_owner(owner),
        }

    def _parse_collapsed_cell(self, cell: str) -> Dict[str, Any] | None:
        """Read a transaction out of a row pdfplumber collapsed into one cell.

        The cell holds the record on its first line and the filing's footnotes
        ("Filing Status", "Subholding Of", "Location") on the rest, with the
        small-caps glyphs rendered as NUL bytes. Everything needed to read it
        already exists: `_join_wrapped_amounts` stitches a range split across
        two lines back together, and `_parse_text_line` reads the result. The
        column-indexed path simply never called them.

        Footnote lines carry no date, no dollar sign and no buy/sell keyword, so
        `_parse_text_line` rejects them and the first successful parse is the
        transaction.
        """
        lines = self._join_wrapped_amounts(str(cell).replace("\x00", "").split("\n"))

        for line in lines:
            txn = self._parse_text_line(self._spell_out_type_letter(line))
            if not txn:
                continue

            # The record carries both dates in order -- the trade, then the
            # notification. `_parse_text_line` takes the first, which is the one
            # STOCK Act compliance is measured from; the second belongs in
            # notification_date rather than being discarded.
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", line)
            if len(dates) > 1:
                txn["notification_date"] = self._parse_date(dates[1])

            # Flagged because it arrived through the weaker text path, which the
            # confidence score reports rather than hides.
            txn["recovered_from_collapsed_row"] = True
            return txn

        return None

    # The transaction-type column sits between the asset and the date, so in a
    # flattened record the single letter immediately before the first date is
    # always the type. Matching the letter anywhere in the line instead would be
    # reckless -- "7.00% Series E" and "Class P" are asset names -- but anchored
    # to the date it is the layout, not a guess.
    _TYPE_LETTER = re.compile(r"\b([PSE])\s+(?=\d{1,2}/\d{1,2}/\d{2,4})")
    _TYPE_WORDS = {"P": "purchase", "S": "sale", "E": "exchange"}

    def _spell_out_type_letter(self, line: str) -> str:
        """Expand the type letter so the text parser recognises an exchange.

        `_parse_text_line` matches "p" and "s" as whole words and so already
        reads purchases and sales, but nothing matches a lone "E". Without this
        every exchange in a collapsed row is dropped -- one of the eighteen in
        the test corpus.
        """
        return self._TYPE_LETTER.sub(
            lambda match: f"{self._TYPE_WORDS[match.group(1)]} ", line, count=1
        )

    def _parse_text(self, text: str) -> List[Dict[str, Any]]:
        """Parse transactions from plain text (fallback method)."""
        transactions = []

        # Common pattern: "ASSET DESCRIPTION  P/S  MM/DD/YYYY  $X - $Y  Owner".
        # The amount range routinely wraps -- "$15,001 -" on one line and
        # "$50,000" on the next -- so a line-at-a-time scan saw only the opening
        # bound and stored no amount at all. Stitch a dangling range back
        # together before parsing.
        lines = self._join_wrapped_amounts(text.split("\n"))

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

    @staticmethod
    def _join_wrapped_amounts(lines: List[str]) -> List[str]:
        """Rejoin amount ranges split across two lines."""
        joined: List[str] = []
        pending: str | None = None

        for raw in lines:
            line = raw.rstrip()
            if pending is not None:
                # The continuation carries the upper bound, but rarely on its
                # own: the asset name wraps too, so the line reads
                # "Common Stock (ACI) [ST] $50,000" or "D Cumulative Perpetual
                # Redeemable $50,000". Taking the whole tail leaves the two
                # halves of the band separated by that text, and the range never
                # matches -- which is why 60 of 200 real filings had a
                # transaction with no amount at all. Take the first figure
                # instead, and keep the rest so the description is not lost.
                tail = re.sub(r"^\s*\[[A-Z]{1,5}\]\s*", "", line).strip()
                bound = re.search(r"\$[\d,]+", tail)
                if bound:
                    rest = (tail[: bound.start()] + " " + tail[bound.end() :]).strip()
                    joined.append(f"{pending} {bound.group(0)} {rest}".strip())
                else:
                    joined.append(f"{pending} {tail}".strip())
                pending = None
                continue

            # A range that opens but does not close on this line -- either
            # "$15,001 -" with the upper bound overleaf, or the open-ended
            # "Over" with its figure overleaf. The second form is how the top
            # band on a spouse line wraps, and without it the amount was lost
            # entirely.
            if re.search(r"\$[\d,]+\s*[-–—]\s*$", line) or re.search(
                r"\b(over|above|more than)\s*$", line, re.IGNORECASE
            ):
                pending = line
                continue

            joined.append(line)

        if pending is not None:
            joined.append(pending)
        return joined

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
        amount_match = re.search(
            r"\$[\d,]+\s*-\s*\$[\d,]+|(?:over|above|more than)\s+\$[\d,]+|\$[\d,]+\s*\+",
            line,
            re.IGNORECASE,
        )

        # Extract transaction type. Positionally first: the type column sits
        # immediately before the date, so the token just before the first date
        # is the type. Scanning the whole line for keywords instead made
        # "Best Buy Co., Inc. Common Stock S 02/23/2024" a PURCHASE, because
        # "Buy" is in the company name -- a disclosed sale recorded backwards.
        txn_type = None
        if date_match:
            before = line[: date_match.start()].rstrip()
            trailing = re.search(r"([A-Za-z]+)\s*$", before)
            if trailing:
                txn_type = self._parse_transaction_type(trailing.group(1))

        if not txn_type:
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
            # Exchanges were checked nowhere in this path, so every one of them
            # was dropped -- the text fallback only ever recognised purchases
            # and sales. `\bexchange\b` does not match "exchanged", which is
            # how the word appears in the footnote prose beneath a record.
            for kw in EXCHANGE_KEYWORDS:
                if re.search(rf"\b{kw}\b", line, re.IGNORECASE):
                    txn_type = "exchange"
                    break

        if not txn_type:
            return None

        # Extract description (everything before date or amount)
        description = line
        if date_match:
            description = line[: date_match.start()].strip()
        elif amount_match:
            description = line[: amount_match.start()].strip()

        # Strip the transaction-type token, and ONLY it. This used to remove
        # every occurrence of every keyword anywhere in the description, so
        # "Best Buy Co., Inc." became "Best Co., Inc." and "Purchase Point Media
        # Corp" became "Point Media Corp" -- a company name mangled by the word
        # it happens to contain, in a field that feeds sector classification and
        # the opacity index.
        #
        # The type column sits immediately before the date, so the token to
        # remove is the last one in the description and nothing else.
        description = re.sub(
            rf"\s*\b(?:{'|'.join(BUY_KEYWORDS + SELL_KEYWORDS + EXCHANGE_KEYWORDS)})\b\s*$",
            "",
            description,
            flags=re.IGNORECASE,
        )
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
        """Parse transaction type from a Transaction Type cell.

        The cell holds a one-letter code, sometimes with a qualifier: "P", "S",
        "E", "S (partial)". The code is the first token, and reading it that way
        is the whole fix here.

        This used to test `keyword in text.lower()` as a plain substring, so
        "S (partial)" matched the "p" of BUY_KEYWORDS inside the word "partial"
        and a disclosed **sale was recorded as a purchase**. Direction is not
        cosmetic: `contract_front_run` only looks at purchases, and the
        cross-member cluster detector groups by it.
        """
        if not text:
            return None

        cleaned = text.lower().strip()
        first = next((token for token in re.split(r"[^a-z]+", cleaned) if token), "")

        codes = {"p": "purchase", "s": "sale", "e": "exchange"}
        if first in codes:
            return codes[first]

        for keywords, kind in (
            (BUY_KEYWORDS, "purchase"),
            (SELL_KEYWORDS, "sale"),
            (EXCHANGE_KEYWORDS, "exchange"),
        ):
            for kw in keywords:
                if re.search(rf"\b{re.escape(kw)}\b", cleaned):
                    return kind

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
        if len(amounts) == 1:
            # "Over $X" and "$X +" are open-ended. Returning (X, X) would turn a
            # lower bound into an exact figure, which is the one thing this
            # project will not do with a disclosure that reports a band.
            if re.search(r"\b(over|above|more than)\b", text, re.IGNORECASE) or "+" in text:
                return Decimal(amounts[0]), None
            return Decimal(amounts[0]), Decimal(amounts[0])

        return None, None

    def _extract_ticker(self, text: str) -> str | None:
        """Extract stock ticker from text."""
        if not text:
            return None

        # Explicit ticker notation like (AAPL) or [MSFT]. Parenthesised codes are
        # tickers; square-bracketed ones are usually the filing's asset-class
        # tag, so those are checked against ASSET_CLASS_CODES as well.
        for match in re.finditer(r"([\(\[])([A-Z]{1,5})([\)\]])", text):
            opener, ticker, _ = match.groups()
            if ticker in NON_TICKERS:
                continue
            if opener == "[" and ticker in ASSET_CLASS_CODES:
                continue
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
