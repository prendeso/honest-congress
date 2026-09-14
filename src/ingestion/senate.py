"""Senate financial disclosure ingestion."""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests

from src.ingestion.base import BaseIngester

logger = logging.getLogger(__name__)


class SenateSearchError(RuntimeError):
    """eFD could not be queried at all.

    Distinct from an empty result on purpose. Every failure path here used to
    `return []`, so a total outage and a year with no filings produced the same
    value -- and `sync_senate_disclosures` reported "Synced 0 Senate disclosures"
    while the workflow step exited green. Senate coverage was zero for months and
    nothing in any run said so.
    """


# Senate eFD search URL
SENATE_EFD_BASE_URL = "https://efdsearch.senate.gov"
SENATE_SEARCH_URL = f"{SENATE_EFD_BASE_URL}/search"
SENATE_DATA_URL = f"{SENATE_EFD_BASE_URL}/search/report/data/"

# Report and filer codes, read from the live search form rather than guessed.
REPORT_TYPE_ANNUAL = "7"
REPORT_TYPE_PTR = "11"
FILER_TYPE_SENATOR = "1"
FILER_TYPE_CANDIDATE = "4"
FILER_TYPE_FORMER_SENATOR = "5"

# A filing's URL carries its own kind. eFD serves several shapes --
# /search/view/ptr/<uuid>/ for an electronically filed trade report,
# /search/view/paper/<uuid>/ for a scan -- and the document id is a UUID, not
# the run of digits this module used to require.
SENATE_VIEW_PATH = re.compile(
    r"/search/view/(?P<kind>[a-z_]+)/(?P<doc_id>[0-9a-fA-F-]{8,}|\d+)/?",
)


# The cell that carries the link is markup, not a label:
#   <a href="/search/view/annual/14c0.../">Annual Report for CY 2023 (Amendment 1)</a>
# Storing it raw overflowed disclosures.filing_type, a VARCHAR(50), and aborted
# the whole ingest. SQLite ignores declared string lengths, so the tests were
# blind to it -- the same Postgres-only blind spot that hid the NUL byte.
_TAGS = re.compile(r"<[^>]+>")
FILING_TYPE_MAX = 50


def _filing_type_label(row: List[Any], is_ptr: bool) -> str:
    """A short, human-readable label that fits the column.

    "PTR" for trade reports, matching what the House path stores so the two
    chambers can be filtered the same way. Otherwise the link's own text, which
    is genuinely informative -- it distinguishes "Annual Report for CY 2023"
    from "(Amendment 1)" -- with the markup stripped and the result bounded.
    """
    if is_ptr:
        return "PTR"

    for cell in row:
        text = _TAGS.sub("", str(cell)).strip()
        if text and "report" in text.lower():
            return text[:FILING_TYPE_MAX]

    return "FD"


def _as_json_array(value: str) -> str:
    """eFD wants `report_types=[11]`, a JSON array in a form field.

    Accepts "", "11", or an already-bracketed "[11]" so callers can pass either
    shape; an empty value means "every type", which the endpoint expresses as an
    empty array.
    """
    value = (value or "").strip()
    if not value:
        return "[]"
    if value.startswith("["):
        return value
    return "[" + ",".join(part.strip() for part in value.split(",") if part.strip()) + "]"


def _is_the_agreement_page(response: requests.Response) -> bool:
    """Whether eFD answered with its prohibition agreement rather than a filing.

    It is a 200 either way, so the status code cannot be used. Two signals, both
    cheap: the request ended up at the search home rather than a `/view/` path,
    or the body carries the agreement's own wording.
    """
    final_url = str(getattr(response, "url", "") or "")
    if "/view/" not in final_url:
        return True
    body = response.text or ""
    return "prohibitions on obtaining and use of financial disclosure" in body.lower()


class SenateIngester(BaseIngester):
    """Ingester for Senate financial disclosures (eFD system)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        self._csrf_token: str | None = None
        self._session_initialized = False

    def _init_session(self) -> bool:
        """Initialize session with CSRF token and agreement acceptance."""
        if self._session_initialized:
            return True

        try:
            # Visit the landing page to get session cookie
            logger.info("Initializing Senate eFD session...")
            response = self.session.get(f"{SENATE_EFD_BASE_URL}/search/home/", timeout=30)
            response.raise_for_status()
            time.sleep(1)  # Rate limiting

            # Accept the agreement. eFD requires a POST of `prohibition_agreement=1`
            # carrying the CSRF token; a GET with `?accept=true` -- what this sent
            # for as long as the file has existed -- leaves the session
            # unaccepted, and every subsequent search answers 503. Verified
            # against the live site: the GET form yields three 503s and the POST
            # form yields 200 with records, from the same machine seconds apart.
            token = self.session.cookies.get("csrftoken", "")
            agree_response = self.session.post(
                f"{SENATE_EFD_BASE_URL}/search/home/",
                data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
                headers={"Referer": f"{SENATE_EFD_BASE_URL}/search/home/"},
                timeout=30,
            )
            agree_response.raise_for_status()
            time.sleep(1)

            # The token is rotated by the POST, so re-read it rather than reusing
            # the pre-agreement value.
            self._csrf_token = self.session.cookies.get("csrftoken", token)

            if self._csrf_token:
                self._session_initialized = True
                logger.info("Senate eFD session initialized successfully")
                return True
            else:
                logger.error("Failed to get CSRF token")
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to initialize Senate session: {e}")
            return False

    def _get_csrf_token(self) -> str | None:
        """Get CSRF token required for Senate eFD searches."""
        if not self._session_initialized:
            self._init_session()
        return self._csrf_token

    def fetch_members(self) -> List[Dict[str, Any]]:
        """
        Fetch list of Senate members from disclosure search.
        Note: Senate eFD doesn't have a clean member API,
        so we rely on Congress.gov for member data.
        """
        return []

    def search_disclosures_ajax(
        self,
        filing_year: int,
        filer_type: str = "1",  # 1=Senator, 2=Candidate
        report_type: str = "",  # Empty for all, specific codes for types
        start: int = 0,
        length: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Search for Senate disclosures using the AJAX data endpoint.

        Args:
            filing_year: Year of filing
            filer_type: 1=Senator, 2=Candidate
            report_type: Report type filter
            start: Pagination start
            length: Number of records to fetch

        Returns:
            List of disclosure metadata dicts
        """
        if not self._init_session():
            raise SenateSearchError(
                "could not establish an eFD session (the agreement POST failed)"
            )

        try:
            # Build the AJAX request
            # `report_types` / `filer_types`, plural, each a JSON array -- not the
            # singular scalars this sent. The endpoint answers 200 either way; it
            # simply matches nothing. The dates need times as well.
            #
            # Codes read from the live search form rather than guessed:
            #   report_types  7=Annual  11=Periodic Transactions  10=Extension
            #                 14=Blind Trusts  15=Other
            #   filer_types   1=Senator  4=Candidate  5=Former Senator
            data = {
                "draw": "1",
                "start": str(start),
                "length": str(length),
                "filer_types": _as_json_array(filer_type),
                "report_types": _as_json_array(report_type),
                "submitted_start_date": f"01/01/{filing_year} 00:00:00",
                "submitted_end_date": f"12/31/{filing_year} 23:59:59",
                "csrfmiddlewaretoken": self._csrf_token or "",
            }

            headers = {
                "X-CSRFToken": self._csrf_token or "",
                "Referer": f"{SENATE_SEARCH_URL}/",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": SENATE_EFD_BASE_URL,
            }

            # Add retry logic
            for attempt in range(3):
                try:
                    response = self.session.post(
                        SENATE_DATA_URL, data=data, headers=headers, timeout=60
                    )

                    if response.status_code == 200:
                        try:
                            json_data = response.json()
                            return self._parse_ajax_results(json_data, filing_year)
                        except ValueError:
                            raise SenateSearchError(
                                "eFD returned a non-JSON body; the session is probably not accepted"
                            ) from None
                    elif response.status_code == 503:
                        logger.warning(f"Senate eFD returned 503 (attempt {attempt + 1}/3)")
                        time.sleep(5 * (attempt + 1))  # Exponential backoff
                        continue
                    else:
                        raise SenateSearchError(f"eFD returned HTTP {response.status_code}")

                except requests.RequestException as e:
                    logger.warning(f"Request failed (attempt {attempt + 1}/3): {e}")
                    time.sleep(5)
                    continue

            raise SenateSearchError("all retry attempts failed")

        except SenateSearchError:
            raise
        except Exception as e:
            raise SenateSearchError(f"unexpected failure querying eFD: {e}") from e

    def _parse_ajax_results(
        self, json_data: Dict[str, Any], filing_year: int
    ) -> List[Dict[str, Any]]:
        """Parse the AJAX JSON response from Senate eFD."""
        disclosures = []

        records_total = json_data.get("recordsTotal", 0)
        data = json_data.get("data", [])

        logger.info(f"Senate eFD returned {records_total} total records, {len(data)} in this page")

        # A live row, for reference:
        #   [0] "David H"
        #   [1] "McCormick"
        #   [2] "McCormick, David H. (Senator)"
        #   [3] '<a href="/search/view/ptr/e337e1b4-...-1d25a81ea26f/">Periodic
        #        Transaction Report for 12/26/2025</a>'
        #   [4] "12/26/2025"
        #
        # This used to read row[-1] as the link -- that is the DATE -- and to
        # require a numeric document id, where eFD uses UUIDs. Measured against
        # 100 live rows, it matched none of them. Rather than swap one hardcoded
        # index for another, scan the row for the first cell containing a view
        # link: the column order is eFD's to change, and a shifted column should
        # cost nothing.
        unparsed = 0

        for row in data:
            if not isinstance(row, list) or len(row) < 5:
                unparsed += 1
                continue

            try:
                match = None
                for cell in row:
                    match = SENATE_VIEW_PATH.search(str(cell))
                    if match:
                        break

                if not match:
                    unparsed += 1
                    continue

                doc_id = match.group("doc_id")
                kind = match.group("kind")

                # The path segment is the FORMAT, not the report kind:
                # /search/view/ptr/ is an electronically filed trade report and
                # /search/view/paper/ is a scan -- and a scanned PTR is still a
                # PTR. A live PTR search returns both, so keying is_ptr on the
                # segment alone silently filed paper trade reports as annual
                # ones, where the wrong parser would read them and the late
                # filing detector would never see them. The link text carries
                # the kind, so use both.
                link_text = " ".join(str(cell) for cell in row).lower()
                is_ptr = kind == "ptr" or "periodic transaction" in link_text

                disclosures.append(
                    {
                        "document_id": f"S{doc_id}",
                        "document_url": f"{SENATE_EFD_BASE_URL}{match.group(0).rstrip('/')}/",
                        "filing_year": filing_year,
                        "chamber": "senate",
                        "first_name": str(row[0]).strip(),
                        "last_name": str(row[1]).strip(),
                        "filer_type": str(row[2]).strip(),
                        "filing_type": _filing_type_label(row, is_ptr),
                        # The trade reports are what the House path calls a PTR.
                        # Without this they would be read by the annual-filing
                        # parser, which looks for a different layout entirely.
                        "is_ptr": is_ptr,
                        "filing_date": self._parse_date(str(row[4])),
                    }
                )
            except Exception as e:
                unparsed += 1
                logger.debug(f"Error parsing row: {e}")
                continue

        if unparsed:
            # Silence here is what let a parser that matched nothing look like a
            # year with no filings.
            logger.warning(
                "Senate eFD: %d of %d rows carried no recognisable document link",
                unparsed,
                len(data),
            )

        return disclosures

    def _parse_date(self, date_str: str) -> datetime | None:
        """Parse date string from Senate eFD."""
        if not date_str:
            return None

        formats = ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    def search_disclosures(
        self,
        first_name: str = "",
        last_name: str = "",
        filing_year: int | None = None,
        report_type: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Search for Senate financial disclosures.

        Note: This method now uses the AJAX endpoint for better reliability.
        """
        year = filing_year or datetime.now().year
        return self.search_disclosures_ajax(year, report_type=report_type)

    def fetch_disclosures(self, member_id: str) -> List[Dict[str, Any]]:
        """Fetch disclosures for a specific senator by last name."""
        return self.search_disclosures(last_name=member_id)

    def search_all_disclosures(self, filing_year: int) -> List[Dict[str, Any]]:
        """Every Senate annual filing and trade report for a year.

        Both kinds, explicitly, and paginated.

        This used to send one request for report_type="" -- all types -- and
        take whatever the first page held. Two things were wrong with that. The
        Senate's trade reports were never asked for as such, so nothing set
        is_ptr and anything that did arrive would have been handed to the
        annual-filing parser. And a single page is a cap: 2025 alone holds 141
        trade reports and 119 annual filings, well past any default page size.
        """
        results: List[Dict[str, Any]] = []

        for report_type in (REPORT_TYPE_ANNUAL, REPORT_TYPE_PTR):
            results.extend(self._search_paginated(filing_year, report_type))

        logger.info(
            "Senate eFD %d: %d filings (%d trade reports)",
            filing_year,
            len(results),
            sum(1 for r in results if r.get("is_ptr")),
        )
        return results

    def _search_paginated(
        self, filing_year: int, report_type: str, page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """Walk every page of one report type.

        Stops on a short page rather than trusting a total, and carries a hard
        page ceiling so a server that ignores `start` cannot loop forever.
        """
        collected: List[Dict[str, Any]] = []
        max_pages = 50

        for page in range(max_pages):
            rows = self.search_disclosures_ajax(
                filing_year,
                filer_type=FILER_TYPE_SENATOR,
                report_type=report_type,
                start=page * page_size,
                length=page_size,
            )
            if not rows:
                break

            collected.extend(rows)

            if len(rows) < page_size:
                break

            time.sleep(1)
        else:
            logger.warning(
                "Senate eFD: stopped at the %d-page ceiling for report type %s in %d",
                max_pages,
                report_type,
                filing_year,
            )

        return collected

    def download_disclosure(self, disclosure_url: str, output_path: str) -> bool:
        """
        Download a Senate disclosure document.

        Note: Senate disclosures are often HTML-based rather than PDF.
        This method handles both cases.

        Args:
            disclosure_url: URL of the disclosure
            output_path: Local path to save the file

        Returns:
            True if successful, False otherwise
        """
        try:
            # eFD serves no document to a session that has not accepted the
            # prohibition agreement -- it answers 200 with the agreement page
            # instead. Every other method here reaches the agreement through
            # `_get_csrf_token`; this one did not, and nothing noticed because
            # the agreement page is a perfectly valid 200.
            #
            # `cli parse` runs in a different process from `cli ingest` and never
            # searches, so its session was ALWAYS unaccepted. Every Senate filing
            # it downloaded was a 12,189-byte copy of the same agreement page,
            # saved as the filing, and `parse_senate_html` then reported "no
            # transaction table" -- which is true of the agreement page and says
            # nothing whatever about the filing.
            #
            # The visible result was 19 of 20 Senate filings stored as parsed,
            # with a text layer, zero transactions, and not one Senate finding on
            # the site after three rebuilds.
            if not self._init_session():
                logger.error(
                    "Senate eFD session could not accept the prohibition agreement; "
                    "refusing to download %s",
                    disclosure_url,
                )
                return False

            response = self.session.get(disclosure_url, timeout=60)
            response.raise_for_status()

            # Redirected back to the search home means the agreement did not
            # stick after all. Saving that page would produce exactly the silent
            # failure above, so it is an error rather than a document.
            if _is_the_agreement_page(response):
                logger.error(
                    "eFD returned the prohibition agreement instead of %s; not saving it",
                    disclosure_url,
                )
                return False

            # Ensure directory exists
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            # Determine file type from content
            content_type = response.headers.get("Content-Type", "")

            if "pdf" in content_type:
                # Binary PDF
                with open(output_path, "wb") as f:
                    f.write(response.content)
            else:
                # HTML content - save as HTML
                html_path = output_path.replace(".pdf", ".html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(response.text)
                output_path = html_path

            logger.info(f"Downloaded Senate disclosure to {output_path}")
            return True

        except requests.RequestException as e:
            logger.error(f"Failed to download {disclosure_url}: {e}")
            return False
        except OSError as e:
            logger.error(f"Failed to save file {output_path}: {e}")
            return False


class SenatePTRIngester(SenateIngester):
    """
    Specialized ingester for Senate Periodic Transaction Reports (PTRs).
    PTRs contain individual stock trades and are filed within 45 days of trades.
    """

    def search_ptr_reports(
        self,
        first_name: str = "",
        last_name: str = "",
        filing_year: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Search specifically for PTR reports."""
        return self.search_disclosures(
            first_name=first_name,
            last_name=last_name,
            filing_year=filing_year,
            report_type="11",  # PTR type code
        )
