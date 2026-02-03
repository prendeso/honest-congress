"""House Clerk HTML scraper for PTR data when XML is unavailable."""
import logging
import re
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://disclosures-clerk.house.gov"
SEARCH_URL = f"{BASE_URL}/FinancialDisclosure/SearchResults"


class HouseClerkScraper:
    """
    Scrapes PTR and financial disclosure data from House Clerk website.

    Use this when the XML endpoints are unavailable (404).
    """

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def search_ptrs(
        self,
        year: int,
        filing_type: str = "P",  # P = PTR
        last_name: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for PTR filings via the House Clerk search form.

        Args:
            year: Filing year
            filing_type: P for PTR, F for FD
            last_name: Optional filter by last name
            state: Optional filter by state

        Returns:
            List of disclosure records
        """
        results = []
        page = 1
        max_pages = 100  # Safety limit

        while page <= max_pages:
            try:
                # Build search parameters
                params = {
                    "searchYear": year,
                    "filingType": filing_type,
                    "page": page,
                }
                if last_name:
                    params["lastName"] = last_name
                if state:
                    params["state"] = state

                logger.info(f"Fetching PTR search page {page} for {year}")
                response = self.session.get(SEARCH_URL, params=params, timeout=30)
                response.raise_for_status()

                page_results = self._parse_search_results(response.text, year)

                if not page_results:
                    logger.info(f"No more results on page {page}")
                    break

                results.extend(page_results)
                logger.info(f"Found {len(page_results)} records on page {page}")

                # Check if there are more pages
                if len(page_results) < 25:  # Typical page size
                    break

                page += 1
                time.sleep(self.delay)

            except Exception as e:
                logger.error(f"Error fetching page {page}: {e}")
                break

        logger.info(f"Total PTRs found for {year}: {len(results)}")
        return results

    def _parse_search_results(self, html: str, year: int) -> List[Dict[str, Any]]:
        """Parse search results HTML into structured records."""
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Find the results table
        table = soup.find("table", class_="table")
        if not table:
            # Try alternative selectors
            table = soup.find("table")

        if not table:
            logger.warning("No results table found in HTML")
            return results

        rows = table.find_all("tr")[1:]  # Skip header row

        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 5:
                    continue

                # Extract data from cells
                # Typical columns: Name, Office, Year, Filing Type, Document
                name_cell = cells[0]
                office_cell = cells[1] if len(cells) > 1 else None
                year_cell = cells[2] if len(cells) > 2 else None
                type_cell = cells[3] if len(cells) > 3 else None
                doc_cell = cells[4] if len(cells) > 4 else None

                # Parse name
                name_text = name_cell.get_text(strip=True)
                last_name, first_name = self._parse_name(name_text)

                # Parse office/state
                office_text = office_cell.get_text(strip=True) if office_cell else ""
                state = self._extract_state(office_text)

                # Get document link
                doc_link = None
                doc_id = None
                if doc_cell:
                    link = doc_cell.find("a", href=True)
                    if link:
                        doc_link = link["href"]
                        if not doc_link.startswith("http"):
                            doc_link = BASE_URL + doc_link
                        # Extract document ID from URL
                        doc_id = self._extract_doc_id(doc_link)

                # Parse filing date if available
                filing_date = None
                date_cell = cells[5] if len(cells) > 5 else None
                if date_cell:
                    date_text = date_cell.get_text(strip=True)
                    filing_date = self._parse_date(date_text)

                if doc_id:
                    results.append({
                        "document_id": doc_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "state": state,
                        "filing_year": year,
                        "filing_type": "PTR",
                        "filing_date": filing_date,
                        "document_url": doc_link,
                        "is_ptr": True,
                    })

            except Exception as e:
                logger.warning(f"Error parsing row: {e}")
                continue

        return results

    def _parse_name(self, name_text: str) -> tuple:
        """Parse 'Last, First' format into separate names."""
        if "," in name_text:
            parts = name_text.split(",", 1)
            return parts[0].strip(), parts[1].strip()
        else:
            # Try to split on space
            parts = name_text.split()
            if len(parts) >= 2:
                return parts[-1], " ".join(parts[:-1])
            return name_text, ""

    def _extract_state(self, office_text: str) -> Optional[str]:
        """Extract state abbreviation from office text."""
        # Look for 2-letter state code
        match = re.search(r'\b([A-Z]{2})\b', office_text)
        if match:
            return match.group(1)
        return None

    def _extract_doc_id(self, url: str) -> Optional[str]:
        """Extract document ID from URL."""
        # Pattern: /public_disc/ptr-pdfs/2024/12345678.pdf
        match = re.search(r'/(\d+)\.pdf', url)
        if match:
            return match.group(1)
        # Alternative pattern
        match = re.search(r'docid=(\d+)', url, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _parse_date(self, date_text: str) -> Optional[datetime]:
        """Parse date string into datetime."""
        formats = ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(date_text, fmt)
            except ValueError:
                continue
        return None

    def fetch_all_ptrs(self, years: List[int]) -> List[Dict[str, Any]]:
        """Fetch PTRs for multiple years."""
        all_results = []
        for year in years:
            results = self.search_ptrs(year)
            all_results.extend(results)
            time.sleep(self.delay)
        return all_results


def scrape_house_ptrs(years: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Convenience function to scrape House PTRs."""
    if years is None:
        years = [datetime.now().year]

    scraper = HouseClerkScraper()
    return scraper.fetch_all_ptrs(years)

