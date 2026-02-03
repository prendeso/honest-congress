"""House of Representatives financial disclosure ingestion."""
import requests
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import re

from src.ingestion.base import BaseIngester

logger = logging.getLogger(__name__)

# House Clerk disclosure search URL
HOUSE_CLERK_BASE_URL = "https://disclosures-clerk.house.gov"
HOUSE_SEARCH_URL = f"{HOUSE_CLERK_BASE_URL}/FinancialDisclosure/Search"
HOUSE_DOWNLOAD_URL = f"{HOUSE_CLERK_BASE_URL}/public_disc/financial-pdfs"
HOUSE_PTR_DOWNLOAD_URL = f"{HOUSE_CLERK_BASE_URL}/public_disc/ptr-pdfs"


class HouseIngester(BaseIngester):
    """Ingester for House of Representatives financial disclosures."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HonestCongress/1.0 (Congressional Disclosure Analyzer)"
        })

    def fetch_members(self) -> List[Dict[str, Any]]:
        """
        Fetch list of House members from disclosure search.
        Note: The House clerk site doesn't have a clean member API,
        so we rely on ProPublica for member data.
        """
        # House clerk site uses form-based search, return empty
        # Members are fetched via ProPublica API
        return []

    def search_disclosures(
        self,
        last_name: str = "",
        filing_year: Optional[int] = None,
        state: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Search for House financial disclosures.

        Args:
            last_name: Member's last name (partial match)
            filing_year: Year of filing (e.g., 2024)
            state: Two-letter state code

        Returns:
            List of disclosure metadata dicts
        """
        try:
            # Build search parameters
            params = {
                "LastName": last_name,
                "FilingYear": filing_year or datetime.now().year,
                "State": state,
            }

            # The House clerk site returns HTML, we need to parse it
            # In production, consider using Selenium or the XML feeds
            response = self.session.get(HOUSE_SEARCH_URL, params=params, timeout=30)
            response.raise_for_status()

            # Parse the response (simplified - actual parsing would be more complex)
            disclosures = self._parse_search_results(response.text)

            logger.info(f"Found {len(disclosures)} House disclosures")
            return disclosures

        except requests.RequestException as e:
            logger.error(f"Failed to search House disclosures: {e}")
            return []

    def fetch_disclosures(self, member_id: str) -> List[Dict[str, Any]]:
        """Fetch disclosures for a specific member by their last name."""
        return self.search_disclosures(last_name=member_id)

    def fetch_annual_xml_index(self, year: int) -> List[Dict[str, Any]]:
        """
        Fetch the annual XML index file that lists all disclosures for a year.
        This is the most reliable way to get House disclosures.

        Args:
            year: Filing year (e.g., 2024)

        Returns:
            List of disclosure records from XML
        """
        xml_url = f"{HOUSE_CLERK_BASE_URL}/public_disc/financial-pdfs/{year}FD.xml"

        try:
            response = self.session.get(xml_url, timeout=60)
            response.raise_for_status()

            disclosures = self._parse_xml_index(response.content, year)
            logger.info(f"Parsed {len(disclosures)} disclosures from {year} XML index")
            return disclosures

        except requests.RequestException as e:
            logger.error(f"Failed to fetch {year} XML index: {e}")
            return []

    def fetch_ptr_xml_index(self, year: int) -> List[Dict[str, Any]]:
        """
        Fetch the Periodic Transaction Report (PTR) XML index for a year.
        PTRs contain individual stock trades filed within 45 days of transaction.

        Args:
            year: Filing year (e.g., 2024)

        Returns:
            List of PTR records from XML
        """
        xml_url = f"{HOUSE_CLERK_BASE_URL}/public_disc/ptr-pdfs/{year}PTR.xml"

        try:
            response = self.session.get(xml_url, timeout=60)
            response.raise_for_status()

            ptrs = self._parse_ptr_xml_index(response.content, year)
            logger.info(f"Parsed {len(ptrs)} PTRs from {year} XML index")
            return ptrs

        except requests.RequestException as e:
            logger.error(f"Failed to fetch {year} PTR XML index: {e}")
            return []

    def _parse_ptr_xml_index(self, xml_content: bytes, year: int) -> List[Dict[str, Any]]:
        """Parse the PTR XML index file."""
        ptrs = []

        try:
            root = ET.fromstring(xml_content)

            for member in root.findall(".//Member"):
                prefix = member.findtext("Prefix", "").strip()
                first = member.findtext("First", "").strip()
                last = member.findtext("Last", "").strip()
                suffix = member.findtext("Suffix", "").strip()

                filing_type = member.findtext("FilingType", "").strip()
                state_dst = member.findtext("StateDst", "").strip()
                filing_date = member.findtext("FilingDate", "").strip()
                doc_id = member.findtext("DocID", "").strip()

                # Parse state and district from StateDst (e.g., "CA05")
                state = state_dst[:2] if len(state_dst) >= 2 else ""
                district = state_dst[2:] if len(state_dst) > 2 else ""

                # Build full name
                name_parts = [prefix, first, last, suffix]
                full_name = " ".join(p for p in name_parts if p)

                # Parse filing date
                try:
                    parsed_date = datetime.strptime(filing_date, "%m/%d/%Y")
                except ValueError:
                    parsed_date = None

                # Build PDF URL for PTR
                pdf_url = f"{HOUSE_PTR_DOWNLOAD_URL}/{year}/{doc_id}.pdf"

                ptrs.append({
                    "first_name": first,
                    "last_name": last,
                    "full_name": full_name,
                    "state": state,
                    "district": district,
                    "filing_type": f"PTR" if not filing_type else f"PTR-{filing_type}",
                    "filing_date": parsed_date,
                    "filing_year": year,
                    "document_id": doc_id,
                    "document_url": pdf_url,
                    "chamber": "house",
                    "is_ptr": True,
                })

        except ET.ParseError as e:
            logger.error(f"Failed to parse PTR XML index: {e}")

        return ptrs

    def _parse_xml_index(self, xml_content: bytes, year: int) -> List[Dict[str, Any]]:
        """Parse the annual XML index file."""
        disclosures = []

        try:
            root = ET.fromstring(xml_content)

            for member in root.findall(".//Member"):
                prefix = member.findtext("Prefix", "").strip()
                first = member.findtext("First", "").strip()
                last = member.findtext("Last", "").strip()
                suffix = member.findtext("Suffix", "").strip()

                filing_type = member.findtext("FilingType", "").strip()
                state_dst = member.findtext("StateDst", "").strip()
                filing_date = member.findtext("FilingDate", "").strip()
                doc_id = member.findtext("DocID", "").strip()

                # Parse state and district from StateDst (e.g., "CA05")
                state = state_dst[:2] if len(state_dst) >= 2 else ""
                district = state_dst[2:] if len(state_dst) > 2 else ""

                # Build full name
                name_parts = [prefix, first, last, suffix]
                full_name = " ".join(p for p in name_parts if p)

                # Parse filing date
                try:
                    parsed_date = datetime.strptime(filing_date, "%m/%d/%Y")
                except ValueError:
                    parsed_date = None

                # Build PDF URL
                pdf_url = f"{HOUSE_DOWNLOAD_URL}/{year}/{doc_id}.pdf"

                disclosures.append({
                    "first_name": first,
                    "last_name": last,
                    "full_name": full_name,
                    "state": state,
                    "district": district,
                    "filing_type": filing_type,
                    "filing_date": parsed_date,
                    "filing_year": year,
                    "document_id": doc_id,
                    "document_url": pdf_url,
                    "chamber": "house",
                })

        except ET.ParseError as e:
            logger.error(f"Failed to parse XML index: {e}")

        return disclosures

    def _parse_search_results(self, html: str) -> List[Dict[str, Any]]:
        """
        Parse HTML search results from House clerk site.
        This is a simplified parser - real implementation would use BeautifulSoup.
        """
        # Placeholder - would need proper HTML parsing
        # For now, prefer XML index method
        return []

    def download_disclosure(self, disclosure_url: str, output_path: str) -> bool:
        """
        Download a disclosure PDF.

        Args:
            disclosure_url: URL of the PDF
            output_path: Local path to save the file

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.session.get(disclosure_url, timeout=60, stream=True)
            response.raise_for_status()

            # Ensure directory exists
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Downloaded disclosure to {output_path}")
            return True

        except requests.RequestException as e:
            logger.error(f"Failed to download {disclosure_url}: {e}")
            return False
        except IOError as e:
            logger.error(f"Failed to save file {output_path}: {e}")
            return False

