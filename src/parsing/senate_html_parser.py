"""Read a Senate eFD filing, which is an HTML table rather than a PDF.

Senate coverage reached the database and still produced nothing. Every stored
Senate filing came back the same way:

    parsed=True  confidence=0.0  transactions=0
    parse_error: "no text layer in PDF - likely a scan"

They are not scans. Senate eFD serves **HTML**; the House Clerk serves PDFs. The
parse path handed the HTML to pdfplumber, which found no text layer and reported
the only explanation it knows. So 458 filings that are machine-readable in the
most literal sense were stored as unreadable paper, and the landing page counted
them as "scans of paper forms, which hold no machine-readable text".

The upside is that eFD's markup is *better* structured than the PDFs this
project fights with. A periodic transaction report is one table with named
columns:

    #  | Transaction Date | Owner | Ticker | Asset Name | Asset Type | Type |
    Amount | Comment

so there is no layout inference, no column-position guessing, and no OCR. Every
value below is read from a named header rather than an index, because a column
order is eFD's to change.

This deliberately subclasses PTRParser instead of restating its work: amount
bands, transaction direction, owner normalisation and asset typing are identical
problems in both chambers, and one of them -- reading "Sale (Partial)" as a sale
rather than a purchase -- was a real bug fixed there that must not be
reintroduced here by a second implementation.
"""

import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from src.parsing.ptr_parser import ParseQuality, PTRParser

logger = logging.getLogger(__name__)

# eFD writes an em dash or "--" where a field does not apply.
_EMPTY = {"", "-", "--", "—", "n/a", "none"}

# Header text -> the key this parser uses. Matched on a normalised substring so
# a renamed "Asset Name" or "Transaction Type" keeps working.
_COLUMN_ALIASES = (
    ("transaction date", "transaction_date"),
    ("date", "transaction_date"),
    ("owner", "owner"),
    ("ticker", "ticker"),
    ("asset name", "description"),
    ("asset type", "asset_type"),
    ("type", "transaction_type"),
    ("amount", "amount"),
    ("comment", "comment"),
)


# eFD serves a scanned paper filing as an HTML page wrapping one GIF per page,
# served from its media host, with page navigation and no table anywhere. The
# host is the whole signal: an electronically filed report carries no image from
# it, and a scan carries one per page. Measured over 43 live filings -- 38
# electronic, 5 paper -- the separation was exact, 0 media images against 4 to 9.
_SCAN_MEDIA_HOST = "efd-media-public.senate.gov"

# What a table must yield before it is a transaction table. `amount` and
# `transaction_date` alone also describe Part 1 Honoraria Payments; `description`
# is the column that separates a disclosed trade from a speaking fee.
_REQUIRED_COLUMNS = frozenset({"amount", "transaction_date", "description"})


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _is_a_page_image_scan(soup: BeautifulSoup) -> bool:
    """Whether this filing is page images rather than a report we can read."""
    return any(_SCAN_MEDIA_HOST in (img.get("src") or "") for img in soup.find_all("img"))


def _is_empty(value: str) -> bool:
    return _clean(value).lower() in _EMPTY


class SenateHtmlParser(PTRParser):
    """Parse the HTML a Senate eFD filing is served as."""

    def parse_senate_html(self, path: str) -> Dict[str, Any]:
        """Read a saved eFD filing. Same result shape as `parse_ptr`."""
        quality = ParseQuality()
        result: Dict[str, Any] = {
            "transactions": [],
            "filer_info": {},
            "filing_date": None,
            "parse_errors": [],
            "quality": quality.as_dict(),
        }

        try:
            markup = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result["parse_errors"].append(f"could not read {path}: {e}")
            result["quality"] = quality.as_dict()
            return result

        try:
            soup = BeautifulSoup(markup, "html.parser")
            text = soup.get_text(" ", strip=True)
            quality.text_extracted = bool(text)

            tables = self._transaction_tables(soup)
            if not tables:
                # Two different things end up here and they were reported as
                # one. A scan is not a parse failure -- there is nothing in it
                # to read -- and an unknown layout is, loudly.
                #
                # `text_extracted` decides which. It is "a property of the
                # document, not of the parse", and `parse_quality_summary`
                # counts a filing with a text layer that yielded nothing as the
                # parser's own failure. Every scan was landing in that bucket,
                # because on an HTML page `bool(text)` is true of eFD's own
                # chrome -- "Skip to main content", "Print View", "Page 1 of 9"
                # -- which says nothing about the filing. So a scan is recorded
                # as having no text layer, which is what it is.
                if _is_a_page_image_scan(soup):
                    quality.text_extracted = False
                    result["parse_errors"].append(
                        "a scanned paper filing: eFD serves it as page images, "
                        "so there is no transaction table to read"
                    )
                else:
                    result["parse_errors"].append(
                        "no transaction table in the filing, and it is not a scan "
                        "-- a layout this parser does not know"
                    )
                result["quality"] = quality.as_dict()
                return result

            quality.tables_found = True

            # Every matching table, not the first. A Senate ANNUAL filing
            # carries Part 4a "Periodic Transaction Report Summary" and Part 4b
            # "Transactions" as two separate tables with different column
            # orders, and taking the first dropped the other silently -- with a
            # clean score, because a table nobody read contributes to neither
            # side of `rows_parsed / rows_detected`.
            #
            # Measured over 81 live annual filings: 1,087 of 2,615 transactions
            # were missing, 41.6% of the corrected total. 23 filings change; 19
            # of them were stored partial at confidence 1.0 with no warning.
            transactions: List[Dict[str, Any]] = []
            for table, indices in tables:
                transactions.extend(self._rows_to_transactions(table, quality, indices))

            result["transactions"] = transactions
            result["quality"] = quality.as_dict()

            logger.info(
                "Parsed %d transactions from %d table(s) in a Senate filing",
                len(transactions),
                len(tables),
            )

        except Exception as e:  # pragma: no cover - defensive, mirrors parse_ptr
            logger.error("Error parsing Senate filing %s: %s", path, e)
            result["parse_errors"].append(str(e))
            result["quality"] = quality.as_dict()

        return result

    def parse_senate_annual(self, path: str) -> Dict[str, Any]:
        """Read a Senate ANNUAL report. Same result shape as `parse_pdf`.

        343 of 343 Senate annual reports in the corpus stored ZERO assets, and
        the reason was not a bug in anything: **nothing in this project had ever
        read one**. `parse_disclosure` dispatches on the file suffix, so every
        Senate filing went to `parse_senate_html`, which looks for transaction
        tables. An annual report has none, so it was recorded as an empty parse
        -- and half of Congress has had no asset data on this site ever since.

        Rick Scott's 2024 annual names 390 holdings in Part 3, including a
        personal residence at $25,000,001 - $50,000,000, and one debt in Part 7
        at $5,000,001 - $25,000,000. The database had none of it.

        eFD's markup is better than the House PDFs: Part 3 is a real table with
        named columns, the asset name is in its own element, and eFD states the
        asset class instead of leaving it to be guessed from a description. What
        it costs is elsewhere -- see `_senate_band` for two bands whose meaning a
        generic reader gets exactly wrong, and for the one that says a spouse's
        holding is worth "over $1,000,000" with no upper bound, which is a real
        ceiling on what Senate wealth can ever be known to be.
        """
        result: Dict[str, Any] = {
            "assets": [],
            "transactions": [],
            "liabilities": [],
            "earned_income": [],
            "positions": [],
            "agreements": [],
            "parse_errors": [],
            "raw_text": "",
        }

        try:
            markup = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result["parse_errors"].append(f"could not read {path}: {e}")
            return result

        try:
            soup = BeautifulSoup(markup, "html.parser")
            result["raw_text"] = soup.get_text(" ", strip=True)

            if _is_a_page_image_scan(soup):
                # Nothing to read, and not this parser's failure. The same
                # distinction `parse_senate_html` draws.
                result["raw_text"] = ""
                result["parse_errors"].append(
                    "a scanned paper filing: eFD serves it as page images, "
                    "so there is no asset table to read"
                )
                return result

            result["assets"] = self._senate_assets(soup)
            result["liabilities"] = self._senate_liabilities(soup)

            logger.info(
                "Parsed %d assets and %d liabilities from a Senate annual report",
                len(result["assets"]),
                len(result["liabilities"]),
            )
        except Exception as e:  # pragma: no cover - defensive, mirrors parse_ptr
            logger.error("Error parsing Senate annual report %s: %s", path, e)
            result["parse_errors"].append(str(e))

        return result

    def _senate_assets(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Part 3, one holding per row."""
        table = _part_table(soup, "Part 3")
        if table is None:
            return []
        columns = _named_columns(table, _ASSET_COLUMNS)
        if "value" not in columns:
            return []

        rows = [(_row_number(cells), cells) for cells in _body_rows(table)]
        itemised = {
            number.split(".")[0]
            for number, cells in rows
            if "." in number and _senate_band(_cell(cells, columns, "value"))[0] is not None
        }

        assets: List[Dict[str, Any]] = []
        for number, cells in rows:
            description = _asset_name(cells)
            if not description:
                continue
            value_min, value_max = _senate_band(_cell(cells, columns, "value"))
            if number and "." not in number and number in itemised:
                # A holding company whose contents eFD lists separately beneath
                # it. Its stated value IS those contents, so counting both sums
                # the container and what is inside it -- the one way an asset
                # reader can OVERstate somebody, and this project has published
                # enough overstatements already.
                #
                # The row is kept rather than dropped, so what is stored still
                # corresponds one-to-one with what the filing lists and the
                # confidence ratio stays meaningful. Only the value is set
                # aside. Rick Scott's 2024 annual has nine such containers and
                # every one of them states "--" anyway, so nothing observed is
                # changed by this; it is here for the filing that does not.
                value_min, value_max = None, None
            income_min, income_max = _senate_band(_cell(cells, columns, "income"))
            assets.append(
                {
                    "description": description,
                    "ticker": self._extract_ticker(description),
                    "asset_type": _senate_asset_type(_cell(cells, columns, "asset_type")),
                    "value_min": value_min,
                    "value_max": value_max,
                    "income_min": income_min,
                    "income_max": income_max,
                }
            )
        return assets

    def _senate_liabilities(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Part 7, one debt per row."""
        table = _part_table(soup, "Part 7")
        if table is None:
            return []
        columns = _named_columns(table, _LIABILITY_COLUMNS)
        if "creditor" not in columns or "amount" not in columns:
            return []

        liabilities: List[Dict[str, Any]] = []
        for cells in _body_rows(table):
            creditor = _cell(cells, columns, "creditor")
            amount_min, amount_max = _senate_band(_cell(cells, columns, "amount"))
            if not creditor or amount_min is None:
                continue
            liabilities.append(
                {
                    "creditor": creditor,
                    "description": _cell(cells, columns, "type"),
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                }
            )
        return liabilities

    def _transaction_tables(self, soup: BeautifulSoup):
        """Every transaction table in the filing, as (table, column index).

        Selection asks the same question the parse does -- does mapping this
        table's headers yield the columns a transaction needs? -- so the two
        cannot disagree about what a transaction table is. They did.

        The old predicate was a substring test over the joined header text,
        `"amount" and ("transaction" or "date")`. It admitted Part 1 Honoraria
        Payments (`# | Date | Activity | Amount | Who Paid? | ... | Comments`),
        which has an amount and a date and no asset. Part 1 precedes Part 4 in
        document order, so on a filing that had one it was the table the
        first-match rule selected -- and then every row failed the
        `if not description: continue` guard below, so the filing parsed to
        zero transactions and reported "no transactions found in a periodic
        transaction report". Measured over 81 live annual filings: 8 carried
        honoraria, and the 4 of those that also held real transaction tables
        stored nothing at all, losing 67 disclosed trades.

        Requiring `description` is what excludes it, and it excludes the other
        near-misses in the same survey by the same rule rather than by a list of
        exceptions: Part 3 Assets has "Value" rather than "Amount" and no date,
        Part 7 Liabilities an amount but no date, Part 5 Gifts a date but a
        "Value".
        """
        found = []
        for table in soup.find_all("table"):
            indices = self._column_index(table)
            if _REQUIRED_COLUMNS <= indices.keys():
                found.append((table, indices))
        return found

    def _column_index(self, table) -> Dict[str, int]:
        """Map our field names onto this table's actual column positions."""
        indices: Dict[str, int] = {}
        headers = [_clean(th.get_text()).lower() for th in table.find_all("th")]

        for position, header in enumerate(headers):
            for alias, field in _COLUMN_ALIASES:
                if field in indices:
                    continue
                if alias in header:
                    indices[field] = position
                    break
        return indices

    @staticmethod
    def _cell_reader(cells, indices: Dict[str, int]):
        """Read a named column out of one row, by header position."""

        def column(field: str) -> str:
            position = indices.get(field)
            if position is None or position >= len(cells):
                return ""
            return _clean(cells[position].get_text())

        return column

    def _rows_to_transactions(
        self, table, quality: ParseQuality, indices: Dict[str, int] | None = None
    ) -> List[Dict[str, Any]]:
        # The caller has already mapped the columns to decide this IS a
        # transaction table; re-deriving them would be a second chance to
        # disagree with that decision.
        if indices is None:
            indices = self._column_index(table)
        if not _REQUIRED_COLUMNS <= indices.keys():
            return []

        # Reading columns by header name is the whole design of this parser --
        # `_column_index` maps every field from the table's own `<th>` text --
        # but it never said so, and `score_ptr_parse` believes the opposite by
        # default. Two consequences, both measured on the live site:
        #
        #   * every Senate filing was published carrying the warning "column
        #     positions assumed, not read from a header row", which is the exact
        #     reverse of what happened;
        #   * confidence was capped at NO_HEADER_CEILING, 0.5, so every Senate
        #     filing permanently matched `parse --min-confidence 1.0`. The
        #     re-parse queue could never converge: every future run would
        #     re-download and re-read the entire Senate corpus to arrive at the
        #     same 0.5 again.
        #
        # Set here rather than in `_column_index` because this is the point at
        # which the headers are known to have yielded the columns the parse
        # actually needs.
        quality.headers_recognised = True

        transactions: List[Dict[str, Any]] = []

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue

            quality.rows_detected += 1

            column = self._cell_reader(cells, indices)

            description = column("description")
            if not description:
                continue

            transaction_date = self._parse_date(column("transaction_date"))
            transaction_type = self._parse_transaction_type(column("transaction_type"))
            amount_min, amount_max = self._parse_amount_range(column("amount"))

            if not transaction_date or not transaction_type:
                # A row missing either is not a disclosed trade this project can
                # stand behind: the date drives every timing detector and the
                # direction drives the rest.
                continue

            ticker = column("ticker")
            if _is_empty(ticker):
                # eFD leaves the column blank for bonds and funds. Fall back to
                # the same extraction the House path uses on the description.
                ticker = self._extract_ticker(description) or None

            transactions.append(
                {
                    "description": description,
                    "ticker": ticker,
                    "asset_type": column("asset_type") or self._determine_asset_type(description),
                    "transaction_type": transaction_type,
                    "transaction_date": transaction_date,
                    "notification_date": None,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "owner": self._normalize_owner(column("owner")),
                }
            )

        # `+=`, not `=`. Both counters are totals across every table in the
        # filing; assigning made the last table win, which on the Rick Scott
        # filing turned a complete 138-row read into rows_detected=138 /
        # rows_parsed=48 -> confidence 0.35 and the false warning "90 row(s)
        # looked like transactions but could not be read". The House PDF path
        # has always accumulated (`ptr_parser.py`); this one did not need to
        # while it only ever read one table.
        quality.rows_parsed += len(transactions)
        return transactions


# What Part 3 and Part 7 call their columns. Matched on the header text so a
# reordering is eFD's to make -- the same rule the transaction tables follow.
_ASSET_COLUMNS = {
    "asset": "description",
    "asset type": "asset_type",
    "owner": "owner",
    "value": "value",
    "income type": "income_type",
    "income": "income",
}
_LIABILITY_COLUMNS = {
    "incurred": "incurred",
    "debtor": "owner",
    "type": "type",
    "amount": "amount",
    "creditor": "creditor",
}

# Bands eFD writes that are not "$A - $B", and that the generic reader gets
# exactly wrong rather than merely missing:
#
#   "None (or less than $1,001)" reads as an EXACT $1,001 -- it is the band for
#   an asset worth less than that, and 116 of Rick Scott's 390 holdings carry
#   it, so a naive read adds $116,116 of wealth that his filing denies.
#
#   "Over $1,000,000 and held independently by spouse or dependent child" reads
#   as an exact $1,000,000 and is in fact unbounded above. 50 of his holdings
#   carry it. Senate rules let a filer stop counting there for a spouse's
#   separate property, which is a real limit on what this project can ever know
#   about Senate wealth, and recording it as a flat million states a number the
#   document does not.
_SENATE_UNBOUNDED = "over $1,000,000 and held independently by spouse or dependent child"
_LESS_THAN = re.compile(r"none\s*\(or less than\s*\$?([\d,]+)\s*\)", re.IGNORECASE)
_OVER = re.compile(r"^over\s*\$?([\d,]+)", re.IGNORECASE)
_BAND = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*-\s*\$\s*([\d,]+(?:\.\d{1,2})?)")

# eFD names the asset class in its own column, which is better evidence than
# guessing from a description. Checked against the longest match first, so
# "Government Securities Municipal Security" is a bond rather than falling
# through on the word "security".
_ASSET_TYPE_BY_PHRASE = (
    ("exchange traded fund", "mutual_fund"),
    ("mutual fund", "mutual_fund"),
    ("government securities", "bond"),
    ("municipal security", "bond"),
    ("treasury", "bond"),
    ("corporate bond", "bond"),
    ("corporate securities stock", "stock"),
    ("stock", "stock"),
    ("real estate", "real_estate"),
    ("bank deposit", "bank_account"),
    ("retirement", "retirement"),
    ("ira", "retirement"),
    ("401", "retirement"),
)


def _senate_band(text: str) -> tuple[Decimal | None, Decimal | None]:
    """One of eFD's value bands, as (min, max). Unknown text yields nothing."""
    value = _clean(text)
    if not value or value.lower() in _EMPTY:
        return None, None
    if value.lower() == _SENATE_UNBOUNDED:
        return Decimal(1000001), None
    less_than = _LESS_THAN.search(value)
    if less_than:
        return Decimal(0), Decimal(less_than.group(1).replace(",", "")) - 1
    band = _BAND.search(value)
    if band:
        return (
            Decimal(band.group(1).replace(",", "")),
            Decimal(band.group(2).replace(",", "")),
        )
    over = _OVER.match(value)
    if over:
        return Decimal(over.group(1).replace(",", "")) + 1, None
    return None, None


def _senate_asset_type(text: str) -> str:
    label = _clean(text).lower()
    for phrase, kind in _ASSET_TYPE_BY_PHRASE:
        if phrase in label:
            return kind
    return "other"


def _part_table(soup: BeautifulSoup, part: str):
    """The table under a numbered Part heading, or None if that Part is absent."""
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if _clean(heading.get_text(" ", strip=True)).startswith(part):
            return heading.find_next("table")
    return None


def _named_columns(table, aliases: Dict[str, str]) -> Dict[str, int]:
    """Column index per field, keyed by what the header says rather than order."""
    header = table.find("thead")
    cells = (header or table).find_all("th")
    columns: Dict[str, int] = {}
    for index, cell in enumerate(cells):
        label = _clean(cell.get_text(" ", strip=True)).lower()
        field = aliases.get(label)
        if field and field not in columns:
            columns[field] = index
    return columns


def _body_rows(table) -> List[List[Any]]:
    body = table.find("tbody") or table
    return [row.find_all("td") for row in body.find_all("tr") if row.find_all("td")]


def _cell(cells: List[Any], columns: Dict[str, int], field: str) -> str:
    index = columns.get(field)
    if index is None or index >= len(cells):
        return ""
    return _clean(cells[index].get_text(" ", strip=True))


def _row_number(cells: List[Any]) -> str:
    """eFD's own row number: "7" for a holding, "7.1" for one inside it."""
    return _clean(cells[0].get_text(" ", strip=True)) if cells else ""


def _asset_name(cells: List[Any]) -> str:
    """The holding's name, without eFD's company and filer-comment annotations.

    The name is in its own element. Taking the cell's whole text instead reads
    "Personal Residence LLC Company: Personal Residence LLC (Naples, FL)
    Description: Holding company for personal residence" as the asset.
    """
    for cell in cells:
        name = cell.find("strong")
        if name is not None:
            return _clean(name.get_text(" ", strip=True))
    return ""


def count_senate_asset_rows(markup: str) -> int:
    """How many holdings Part 3 lists, counted from the markup.

    The Senate counterpart of `count_schedule_a_rows`, and it exists for the
    same reason: a score derived from the parser's own output can only ever say
    "it ran". This counts the rows eFD prints, against however many the parser
    turns into stored holdings, so a header eFD renames or a row shape this
    reader does not know shows up as a confidence below 1.0 instead of as
    silence.
    """
    if not markup:
        return 0
    table = _part_table(BeautifulSoup(markup, "html.parser"), "Part 3")
    return len(_body_rows(table)) if table is not None else 0
