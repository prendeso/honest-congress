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

            table = self._transaction_table(soup)
            if table is None:
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
            transactions = self._rows_to_transactions(table, quality)
            result["transactions"] = transactions
            result["quality"] = quality.as_dict()

            logger.info("Parsed %d transactions from Senate filing", len(transactions))

        except Exception as e:  # pragma: no cover - defensive, mirrors parse_ptr
            logger.error("Error parsing Senate filing %s: %s", path, e)
            result["parse_errors"].append(str(e))
            result["quality"] = quality.as_dict()

        return result

    def _transaction_table(self, soup: BeautifulSoup):
        """The table whose headers look like a transaction report."""
        for table in soup.find_all("table"):
            headers = [_clean(th.get_text()).lower() for th in table.find_all("th")]
            joined = " ".join(headers)
            if "amount" in joined and ("transaction" in joined or "date" in joined):
                return table
        return None

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

    def _rows_to_transactions(self, table, quality: ParseQuality) -> List[Dict[str, Any]]:
        indices = self._column_index(table)
        if "amount" not in indices or "transaction_date" not in indices:
            quality.rows_detected = 0
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

        quality.rows_parsed = len(transactions)
        return transactions
