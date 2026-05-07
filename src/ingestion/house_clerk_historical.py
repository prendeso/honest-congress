"""
Ingest House Clerk FD data from all available years (2004-2024).
Downloads XML index files and parses financial disclosures.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List

import requests

from src.db.database import SessionLocal
from src.db.models import Chamber, Disclosure, Member
from src.ingestion.date_utils import choose_filing_date

logger = logging.getLogger(__name__)


class HouseClerkHistorical:
    """Download and parse historical House FD disclosures from House Clerk."""

    BASE_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "HonestCongress/1.0 Congressional Analyzer"})

    def get_available_years(self) -> List[int]:
        """Find which years have FD XML files available."""
        available = []

        logger.info("Checking House Clerk for available FD years...")
        for year in range(2004, datetime.now().year + 1):
            url = f"{self.BASE_URL}/{year}FD.xml"
            try:
                r = self.session.head(url, timeout=5)
                if r.status_code == 200:
                    available.append(year)
                    logger.info(f"  {year}: ✓ Available")
                else:
                    logger.debug(f"  {year}: Not found (HTTP {r.status_code})")
            except Exception as e:
                logger.debug(f"  {year}: Error - {str(e)[:50]}")

        logger.info(f"Found {len(available)} available years: {available}")
        return available

    def download_xml(self, year: int) -> bytes | None:
        """Download XML index file for a specific year."""
        url = f"{self.BASE_URL}/{year}FD.xml"

        try:
            logger.info(f"Downloading {year} House FD XML...")
            r = self.session.get(url, timeout=30)
            r.raise_for_status()

            logger.info(f"  ✓ Downloaded ({len(r.content)} bytes)")
            return r.content
        except Exception as e:
            logger.error(f"  ✗ Failed to download: {str(e)}")
            return None

    def parse_xml(self, xml_content: bytes) -> List[Dict]:
        """Parse XML and extract disclosure records."""
        records = []

        try:
            root = ET.fromstring(xml_content)

            # Parse member records - actual structure has Member as root child
            for member_elem in root.findall("Member"):
                last_name = member_elem.findtext("Last", "")
                first_name = member_elem.findtext("First", "")
                suffix = member_elem.findtext("Suffix", "")
                state_dst = member_elem.findtext("StateDst", "")
                year = member_elem.findtext("Year", "")
                doc_id = member_elem.findtext("DocID", "")

                if not first_name or not last_name:
                    continue

                records.append(
                    {
                        "first_name": first_name.strip(),
                        "last_name": last_name.strip(),
                        "suffix": suffix.strip() if suffix else "",
                        "state_dst": state_dst.strip(),
                        "year": int(year) if year else None,
                        "doc_id": doc_id.strip(),
                        "name": f"{last_name}, {first_name}",
                    }
                )

            logger.info(f"  Parsed {len(records)} disclosure records")
            return records
        except ET.ParseError as e:
            logger.error(f"  ✗ XML parse error: {str(e)}")
            return []

    def ingest_year(self, year: int, db_session) -> Dict:
        """Download and ingest all FD for a specific year."""
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Ingesting House FD for {year}")
        logger.info(f"{'=' * 70}")

        # Download XML
        xml_content = self.download_xml(year)
        if not xml_content:
            return {"imported": 0, "errors": 1, "skipped": 0}

        # Parse XML
        records = self.parse_xml(xml_content)
        if not records:
            return {"imported": 0, "errors": 1, "skipped": 0}

        # Import records
        imported = 0
        errors = 0
        skipped = 0

        for record in records:
            try:
                # Find member by name (House Clerk doesn't provide bioguide ID in XML)
                member = (
                    db_session.query(Member)
                    .filter(
                        (Member.last_name == record["last_name"])
                        & (Member.first_name == record["first_name"])
                        & (Member.chamber == Chamber.HOUSE)
                    )
                    .first()
                )

                if not member:
                    logger.debug(f"  Skipped: Member not found ({record['name']})")
                    skipped += 1
                    continue

                # Check for duplicate
                existing = (
                    db_session.query(Disclosure)
                    .filter(Disclosure.document_id == record["doc_id"])
                    .first()
                )

                if existing:
                    logger.debug(f"  Duplicate: {record['name']} {year}")
                    skipped += 1
                    continue

                # Create disclosure record
                disclosure = Disclosure(
                    member_id=member.id,
                    filing_year=record["year"],
                    filing_type="FD",
                    filing_date=choose_filing_date(
                        datetime(record["year"], 12, 31) if record["year"] else None,
                        record["year"],
                    ),
                    document_id=record["doc_id"],
                    document_url=f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{record['year']}/{record['doc_id']}.pdf",
                    is_ptr=False,
                    parsed=False,
                )

                db_session.add(disclosure)
                imported += 1

            except Exception as e:
                logger.error(f"  Error processing record: {str(e)[:50]}")
                errors += 1

        db_session.commit()

        logger.info(f"\nResults for {year}:")
        logger.info(f"  Imported: {imported}")
        logger.info(f"  Skipped: {skipped}")
        logger.info(f"  Errors: {errors}")

        return {"imported": imported, "errors": errors, "skipped": skipped}

    def ingest_all(self, years: List[int] | None = None):
        """Ingest all available House FD data."""
        db = SessionLocal()

        try:
            # Get available years
            if years is None:
                years = self.get_available_years()

            if not years:
                logger.warning("No years available!")
                return

            logger.info(f"\nStarting ingestion for {len(years)} years...")

            total_imported = 0
            total_errors = 0
            total_skipped = 0

            for year in sorted(years):
                result = self.ingest_year(year, db)
                total_imported += result["imported"]
                total_errors += result["errors"]
                total_skipped += result["skipped"]

            logger.info(f"\n{'=' * 70}")
            logger.info("FINAL SUMMARY - House Clerk FD (All Years)")
            logger.info(f"{'=' * 70}")
            logger.info(f"Total Imported: {total_imported}")
            logger.info(f"Total Skipped:  {total_skipped}")
            logger.info(f"Total Errors:   {total_errors}")
            logger.info(f"{'=' * 70}\n")

        finally:
            db.close()


def main():
    """Run House Clerk historical ingestion."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    ingester = HouseClerkHistorical()
    ingester.ingest_all()


if __name__ == "__main__":
    main()
