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
