"""Senate financial disclosure ingestion."""
import requests
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path
import re

from src.ingestion.base import BaseIngester

logger = logging.getLogger(__name__)

# Senate eFD search URL
SENATE_EFD_BASE_URL = "https://efdsearch.senate.gov"
SENATE_SEARCH_URL = f"{SENATE_EFD_BASE_URL}/search"
SENATE_REPORT_URL = f"{SENATE_EFD_BASE_URL}/search/view/paper"
SENATE_DATA_URL = f"{SENATE_EFD_BASE_URL}/search/report/data/"


class SenateIngester(BaseIngester):
    """Ingester for Senate financial disclosures (eFD system)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        self._csrf_token: Optional[str] = None
        self._session_initialized = False

    def _init_session(self) -> bool:
        """Initialize session with CSRF token and agreement acceptance."""
        if self._session_initialized:
            return True

        try:
            # Visit the landing page to get session cookie
            logger.info("Initializing Senate eFD session...")
            response = self.session.get(
                f"{SENATE_EFD_BASE_URL}/search/home/",
                timeout=30
            )
            response.raise_for_status()
            time.sleep(1)  # Rate limiting

            # Accept the agreement (required)
            agree_response = self.session.get(
                f"{SENATE_EFD_BASE_URL}/search/home/",
                params={"accept": "true"},
                timeout=30
            )
            agree_response.raise_for_status()
            time.sleep(1)

            # Extract CSRF token
            self._csrf_token = self.session.cookies.get("csrftoken", "")

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

    def _get_csrf_token(self) -> Optional[str]:
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
        report_type: str = "",   # Empty for all, specific codes for types
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
            logger.error("Failed to initialize session")
            return []

        try:
            # Build the AJAX request
            data = {
                "draw": "1",
                "start": str(start),
                "length": str(length),
                "filer_type": filer_type,
                "report_type": report_type,
                "submitted_start_date": f"01/01/{filing_year}",
                "submitted_end_date": f"12/31/{filing_year}",
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
                        SENATE_DATA_URL,
                        data=data,
                        headers=headers,
                        timeout=60
                    )

                    if response.status_code == 200:
                        try:
                            json_data = response.json()
                            return self._parse_ajax_results(json_data, filing_year)
                        except ValueError:
                            logger.warning(f"Non-JSON response from Senate AJAX")
                            return []
                    elif response.status_code == 503:
                        logger.warning(f"Senate eFD returned 503 (attempt {attempt + 1}/3)")
                        time.sleep(5 * (attempt + 1))  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Senate eFD returned {response.status_code}")
                        return []

                except requests.RequestException as e:
                    logger.warning(f"Request failed (attempt {attempt + 1}/3): {e}")
                    time.sleep(5)
                    continue

            logger.error("All retry attempts failed for Senate search")
            return []

        except Exception as e:
            logger.error(f"Failed to search Senate disclosures: {e}")
            return []

    def _parse_ajax_results(
        self,
        json_data: Dict[str, Any],
        filing_year: int
    ) -> List[Dict[str, Any]]:
        """Parse the AJAX JSON response from Senate eFD."""
        disclosures = []

        records_total = json_data.get("recordsTotal", 0)
        data = json_data.get("data", [])

        logger.info(f"Senate eFD returned {records_total} total records, {len(data)} in this page")

        for row in data:
            # AJAX response returns array of values per row
            # Typical format: [first_name, last_name, filer_type, report_type, date, link]
            if not isinstance(row, list) or len(row) < 5:
                continue

            try:
                # Extract document ID from the link (last element usually contains HTML link)
                link_html = str(row[-1]) if row else ""
                doc_id_match = re.search(r'/search/view/paper/(\d+)/', link_html)

                if not doc_id_match:
                    continue

                doc_id = doc_id_match.group(1)

                disclosures.append({
                    "document_id": doc_id,
                    "document_url": f"{SENATE_REPORT_URL}/{doc_id}/",
                    "filing_year": filing_year,
                    "chamber": "senate",
                    "first_name": str(row[0]).strip() if len(row) > 0 else "",
                    "last_name": str(row[1]).strip() if len(row) > 1 else "",
                    "filer_type": str(row[2]).strip() if len(row) > 2 else "",
                    "filing_type": str(row[3]).strip() if len(row) > 3 else "",
                    "filing_date": self._parse_date(str(row[4])) if len(row) > 4 else None,
                })
            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue

        return disclosures

    def _parse_date(self, date_str: str) -> Optional[datetime]:
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
        filing_year: Optional[int] = None,
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
        """
        Search for all Senate disclosures in a given year.

        Args:
            filing_year: Year of filing

        Returns:
            List of all disclosure metadata for the year
        """
        return self.search_disclosures(filing_year=filing_year)

    def _parse_search_results(
        self,
        html: str,
        filing_year: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Parse HTML search results from Senate eFD site.

        Note: The actual Senate eFD returns data in a table format.
        This is a simplified parser - real implementation would use BeautifulSoup.
        """
        disclosures = []

        # Simple regex-based parsing (would use BeautifulSoup in production)
        # Pattern to find disclosure links in the HTML
        # Format: /search/view/paper/XXXXX/
        pattern = r'/search/view/paper/(\d+)/'
        doc_ids = re.findall(pattern, html)

        # Pattern to extract table row data (simplified)
        # In production, use BeautifulSoup to properly parse the table
        row_pattern = r'<tr[^>]*>.*?</tr>'

        for doc_id in set(doc_ids):
            disclosures.append({
                "document_id": doc_id,
                "document_url": f"{SENATE_REPORT_URL}/{doc_id}/",
                "filing_year": filing_year or datetime.now().year,
                "chamber": "senate",
                # Other fields would be populated from actual HTML parsing
                "first_name": "",
                "last_name": "",
                "state": "",
                "filing_type": "",
                "filing_date": None,
            })

        return disclosures

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
            response = self.session.get(disclosure_url, timeout=60)
            response.raise_for_status()

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
        except IOError as e:
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
        filing_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Search specifically for PTR reports."""
        return self.search_disclosures(
            first_name=first_name,
            last_name=last_name,
            filing_year=filing_year,
            report_type="11",  # PTR type code
        )

