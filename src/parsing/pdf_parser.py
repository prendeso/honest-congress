"""PDF parsing for financial disclosures."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pdfplumber

from src.parsing.text_cleanup import clean_tables, clean_text

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

# The asset-class codes the House annual form prints in square brackets beside
# every Schedule A holding -- BA bank account, ST stock, MF mutual fund, RP real
# property, HE hedge fund, and so on. Every holding carries exactly one, which
# makes counting them an independent measure of how many rows the document
# HOLDS, against however many the parser managed to STORE.
#
# Independent is the whole point. Confidence derived from the parser's own
# output can only ever say "it ran"; that is what `score_fd_parse` used to do,
# and it reported 1.0 on every one of the 927 House annual filings in the corpus
# while capturing about half their holdings.
_SCHEDULE_A_ASSET_CODE = re.compile(
    r"\[(?:BA|ST|MF|OT|RP|HE|PS|EF|IH|FA|GS|WU|CS|PE|DO|OL|OI|TR|VA|IC|AB|BK|CO|EQ|FU|SA)\]"
)

# `clean_text` reduces "SCHEDULE A: ASSETS AND "UNEARNED" INCOME" to `S A: A "U" I`,
# so both spellings have to be accepted.
_SCHEDULE_A_HEADING = re.compile(r"^\s*S(?:CHEDULE)?\s+A\s*:", re.MULTILINE | re.IGNORECASE)
_SCHEDULE_B_HEADING = re.compile(r"^\s*S(?:CHEDULE)?\s+B\s*:", re.MULTILINE | re.IGNORECASE)


def count_schedule_a_rows(text: str) -> int:
    """How many Schedule A holdings the document appears to contain.

    Measured on the text layer, deliberately, because the table layer is what
    loses them: pdfplumber simply does not find some holdings as table rows, and
    the parser has no other way to see them. On Earl Carter's 2024 annual
    (document 10066714) the Schedule A region names 48 holdings and the parser
    stores 22 -- "Guardian Point Capital [HE] $5,000,001 - $25,000,000" and
    "Ameris Bank [BA] $1,000,001 - $5,000,000" appear in the text and in no
    table at all.

    Bounded to the Schedule A region so Schedule B's trades, which carry the
    same codes, are not counted as holdings.

    Both spellings of the heading are accepted because `clean_text` mangles it:
    "SCHEDULE A: ASSETS AND "UNEARNED" INCOME" survives cleaning as `S A: A "U" I`.
    Matching only the uppercase form found the region in the raw page text and
    never in `raw_text`, which is the string this is actually called with --
    caught by running it against Carter's real filing end to end, where it
    scored a perfect 1.0 by detecting zero rows.
    """
    if not text:
        return 0
    start = _SCHEDULE_A_HEADING.search(text)
    if start is None:
        return 0
    rest = text[start.end() :]
    end = _SCHEDULE_B_HEADING.search(rest)
    region = rest[: end.start()] if end else rest
    return len(_SCHEDULE_A_ASSET_CODE.findall(region))


# Common ticker patterns
TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")
# The House Clerk's asset-class code, which trails a description in square
# brackets -- "[BA]" bank account, "[MF]" mutual fund, "[ST]" stock. Never a
# ticker, and several of the codes are real symbols. See `_extract_ticker`.
#
# Exactly two letters, which is what every code on the form is and what all 122
# fabricated tickers in the sample were. Deliberately not `{1,5}`: that would
# also swallow a bracketed "[MSFT]", and nothing observed says the form never
# does that -- only that what it demonstrably does is the two-letter code.
CLASS_CODE = re.compile(r"\[[A-Z]{2}\]")
STOCK_KEYWORDS = ["common stock", "stock", "shares", "equity"]


# Which lettered schedule a table's header row opens, or None if it opens none.
#
# Matched on the column names the Clerk's form actually prints, taken from the
# extracted tables rather than from the form's documentation, because what
# matters is what pdfplumber hands over. Schedules A and B both start with an
# "Asset" column, so A is identified by "value of asset" and B by its date and
# transaction-type columns -- reading either by the bare word "asset" is the
# defect this exists to prevent.
_SCHEDULE_HEADERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("A", ("asset", "value of asset")),
    ("B", ("asset", "tx.", "amount")),
    ("C", ("source", "type", "amount")),
    ("D", ("creditor", "date incurred")),
    ("E", ("position", "organization")),
    ("G", ("source", "value")),
)

# Continuation lines the form prints beneath an entry: Location, Description,
# Comments. They arrive as their own table rows and were being stored as
# assets -- one filing held 112 copies of "D: Independent professionally
# managed account." Real entries never take this shape.
_CONTINUATION_LINE = re.compile(r"^[A-Z]:\s")

# The owner codes the House form prints in Schedule D's first column: self,
# spouse, joint, dependent child. Anything else in that position is a creditor.
_OWNER_CODE = re.compile(r"(?i)(SELF|SP|JT|DC)")

# Schedule D's "Date Incurred" column, which sits between the creditor and the
# type of debt.
_DATE_INCURRED = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}")


def _schedule_of_header(header: str) -> str | None:
    """The schedule this header opens. Order matters: A before B."""
    for schedule, required in _SCHEDULE_HEADERS:
        if all(word in header for word in required):
            return schedule
    return None


def _is_a_continuation_line(description: str | None) -> bool:
    return bool(description and _CONTINUATION_LINE.match(description.strip()))


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
        result: Dict[str, Any] = {
            "assets": [],
            "transactions": [],
            "liabilities": [],
            "earned_income": [],
            "positions": [],
            "agreements": [],
            "parse_errors": [],
            # Whether the PDF had a text layer, which is a property of the
            # DOCUMENT and not of this parse. `orchestrator.parse_disclosure`
            # has always read `raw_text` here to decide it -- and this dict
            # never carried the key, so the expression fell through to
            # `bool(assets or liabilities)` and the answer became "did the
            # parser find anything".
            #
            # The cost was a category error in the direction that hides work.
            # `score_fd_parse` has two branches, "no text layer in PDF - likely
            # a scan" and "no assets or liabilities found in an annual filing",
            # and the second was unreachable from the orchestrator: a readable
            # filing the parser failed on was stored as a scan, which is the
            # verdict that says nobody is at fault. Measured over 40 randomly
            # sampled type-O House annual filings, 3 were genuine scans and 3
            # were this -- half of what the database called scans were not.
            "raw_text": "",
        }

        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = ""
                tables = []

                for page in pdf.pages:
                    # Extract text. Sanitised here, at the one point every
                    # stored string comes through, rather than at each field.
                    page_text = clean_text(page.extract_text())
                    text += page_text + "\n"

                    # Extract tables
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(clean_tables(page_tables))

                # Recorded before any section parsing, so it cannot be
                # confused with whether the sections yielded anything.
                result["raw_text"] = text

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
        """Parse the assets/Schedule A section.

        Two things went wrong here and they compounded.

        **Schedule B was read as Schedule A.** The test was
        `"asset" in header_text or "value" in header_text`, and BOTH schedules
        on the House annual form begin with an Asset column::

            A  ['Asset','Owner','Value of Asset','Income Type(s)','Income', ...]
            B  ['Asset','Owner','Date','Tx. Type','Amount','Cap. Gains > $200?']

        So every TRANSACTION in an annual filing was stored as a holding, with
        the trade's Amount as the holding's value. `wealth_analyzer` sums
        `value_min`/`value_max` over `Asset` rows to build `net_worth_estimate`,
        so a member who traded one $15,000 position fifty times gained $750,000
        of "net worth". Measured over 40 House annual filings, every one scored
        at confidence 1.0: of the $402,905,378 of net worth they contribute,
        **$338,590,568 -- 84% -- came from Schedule B**.

        **Schedule A's own rows were being dropped.** pdfplumber fragments these
        tables: the schedule header comes back as a table with no data rows, and
        each following fragment has a DATA ROW as its header, which the test
        above then skips. Of 2,499 real Schedule A holdings across the same 40
        filings, 219 were captured -- 8.8%. Rosa DeLauro's four-page filing lists
        a Schedule A and yielded nothing at all.

        Both are fixed by reading the document the way it is laid out: recognise
        each schedule by its own header, remember which one is open, and treat an
        unrecognised table as a continuation of it.
        """
        assets: List[Dict[str, Any]] = []

        for table, schedule in self._tables_by_schedule(tables):
            if schedule != "A":
                continue
            for row in table:
                asset = self._parse_asset_row(row)
                if asset and not _is_a_continuation_line(asset.get("description")):
                    assets.append(asset)

        # The text fallback runs only when the table path found nothing at all.
        # It used to run unconditionally and add to whatever the tables gave,
        # which is double counting by construction.
        if not assets:
            assets.extend(self._extract_assets_from_text(text))

        return assets

    @staticmethod
    def _tables_by_schedule(
        tables: List[List[List[str]]],
    ) -> List[tuple[List[List[str]], str | None]]:
        """Each table's data rows, paired with the schedule they belong to.

        The House annual form is a sequence of lettered schedules, and
        pdfplumber does not hand them over whole -- it fragments them, and a
        fragment carries no header to identify itself. Reading each table in
        isolation therefore cannot tell a holding from a trade from a mortgage.
        Document order can: a header opens a schedule, and everything after it
        belongs to that schedule until the next header.
        """
        out: List[tuple[List[List[str]], str | None]] = []
        current: str | None = None

        for table in tables:
            if not table:
                continue
            header = " ".join(str(cell).lower() for cell in table[0] if cell)
            opened = _schedule_of_header(header)
            if opened is not None:
                current = opened
                rows = table[1:]
            elif current is not None:
                # A fragment: it has no header, so every row is data.
                rows = table
            else:
                continue
            if rows:
                out.append((rows, current))

        return out

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
        """Parse the liabilities/Schedule D section.

        Fragmented exactly as Schedule A is, and missed for the same reason:
        the header table carries no data rows, and each debt arrives as its own
        table with a DATA ROW where the header should be. The old test looked
        for "creditor" in that header and so matched only the empty one.

        This direction is the one that flatters. `_calculate_wealth_progression`
        SUBTRACTS liabilities, so a debt the parser cannot see raises the
        member's apparent net worth -- and net worth is what
        `excessive_wealth_growth` publishes. Rosa DeLauro's filing discloses a
        $250,001-$500,000 mortgage and a $16,508 card balance; the database had
        neither.
        """
        liabilities = []

        for table, schedule in self._tables_by_schedule(tables):
            if schedule != "D":
                continue
            for row in table:
                liability = self._parse_liability_row(row)
                if liability and not _is_a_continuation_line(liability.get("creditor")):
                    liabilities.append(liability)

        return liabilities

    def _parse_liability_row(self, row: List[Any]) -> Dict[str, Any] | None:
        """Parse a single liability row."""
        if not row or len(row) < 2:
            return None

        row = [str(cell).strip() if cell else "" for cell in row]

        # Schedule D is `Owner | Creditor | Date Incurred | Type | Amount`, so
        # the first column is an owner CODE and not a creditor. Reading row[0]
        # stored every debt in the database against a creditor named "JT" or
        # "SP", with the real lender demoted to the description.
        start = 1 if len(row) >= 4 and _OWNER_CODE.fullmatch(row[0]) else 0
        creditor = row[start] if len(row) > start else ""
        amount_text = ""
        description = ""

        for cell in row[start + 1 :]:
            if self._looks_like_value_range(cell):
                amount_text = cell
            elif cell and not description and not _DATE_INCURRED.fullmatch(cell):
                # "Date Incurred" sits between the creditor and the type, so
                # taking the first non-amount cell described every mortgage as
                # "7/29/1999". The type ("Mortgage on Personal Residence") is
                # the next one along and is what a reader needs.
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
        """Extract a stock ticker from an asset description.

        On the House Clerk's annual form the two bracket styles mean different
        things, and this treated them as one:

            Lazard International Strategic Equity Ptf Insti Shs (LISIX) [MF]
                                                                 ^^^^^   ^^
                                                                 ticker  class

        Parentheses hold the security's symbol. **Square brackets hold the
        form's own asset-class code** -- BA bank account, MF mutual fund, ST
        stock, RP real property, and so on -- which is never a ticker. The
        parser already reads that code's meaning separately, in
        `_determine_asset_type`.

        Accepting `[..]` manufactured a symbol for every asset that had no
        symbol to give, and the codes collide with real, heavily traded ones:
        BA is Boeing, GS is Goldman Sachs, WU is Western Union, CS and PE and
        RP are all listed somewhere. So "Fifth-Third Bank [BA]" was stored as a
        Boeing holding.

        Measured over 40 randomly sampled type-O House annual filings, by which
        bracket the symbol came from:

            (parentheses)   514   XLY, IEFA, VDC, VGT, FCTDX, ITOT, SWVXX ...
            [square]        122   BA 50, CS 29, OT 21, MF 9, WU 4, GS 2 ...

        Every one of the 122 was a class code. Not one was a security. That is
        19% of all extracted asset tickers, and `BA` alone was the single most
        common "ticker" in the sample.

        This is the same mistake D3 records against the old committee detector,
        which substring-matched and so read "ba" as Alibaba. That fixed the
        matching; this is the extraction still inventing the symbol.
        """
        explicit = re.search(r"\(([A-Z]{1,5})\)", text)
        if explicit:
            return explicit.group(1)

        # A bracketed symbol that is NOT a two-letter class code is still read,
        # so "Microsoft [MSFT] stock" keeps working.
        bracketed = re.search(r"\[([A-Z]{1,5})\]", CLASS_CODE.sub(" ", text))
        if bracketed:
            return bracketed.group(1)

        # The keyword scan below reads bare capitals out of the description, so
        # the class code has to go first or it is simply harvested there
        # instead: "i shares tr gbl msci [CS]" matches on "shares" and yields
        # CS, and "International Equity [OT]" on "equity" and yields OT.
        text = CLASS_CODE.sub(" ", text)

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

        # Try to parse custom range.
        #
        # The decimal part is matched deliberately. `([\d,]+)` stopped at the
        # point, so an EXACT amount came back as two numbers and was read as a
        # range between them: "$209,630.10" became $10 to $209,630, and
        # "$16,508.00" became a liability of somewhere between ZERO and $16,508.
        # Both figures are real -- filers report exact values for bank balances
        # and credit-card debts -- and both feed the net-worth sums that
        # `excessive_wealth_growth` publishes.
        amounts = [
            Decimal(match.replace(",", ""))
            for match in re.findall(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", text)
            if match.strip(",.")
        ]

        if len(amounts) >= 2:
            return min(amounts), max(amounts)
        elif len(amounts) == 1:
            return amounts[0], amounts[0]

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
