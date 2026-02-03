"""
Scrape Senate eFD database using API.
The Senate eFD website uses an undocumented API - let's use it directly!
"""
import logging
import requests
import json
from typing import List, Dict, Optional
from datetime import datetime
from src.db.database import SessionLocal
from src.db.models import Disclosure, Member, Chamber
from src.ingestion.date_utils import choose_filing_date

logger = logging.getLogger(__name__)

class SenateEFDAPI:
    """Use Senate eFD API to fetch disclosures"""

    # The website calls this API endpoint
    API_URL = "https://efdsearch.senate.gov/api"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://efdsearch.senate.gov/",
        })

    def search_filings(self, last_name: Optional[str] = None, year: Optional[int] = None, filing_type: Optional[str] = None) -> List[Dict]:
        """Search for filings using the API."""
        filings = []

        try:
            # Build search parameters
            params = {}
            if last_name:
                params["lastName"] = last_name
            if year:
                params["year"] = year
            if filing_type:
                params["filingType"] = filing_type

            # Try different endpoint possibilities
            endpoints = [
                "/filings",
                "/search",
                "/searchFilings",
            ]

            for endpoint in endpoints:
                try:
                    url = f"{self.API_URL}{endpoint}"
                    logger.debug(f"  Trying {endpoint}...")

                    r = self.session.get(url, params=params, timeout=10)

                    if r.status_code == 200:
                        data = r.json()

                        # Handle different response structures
                        if isinstance(data, list):
                            filings = data
                        elif isinstance(data, dict):
                            # Try common keys
                            for key in ["filings", "results", "data", "hits"]:
                                if key in data:
                                    filings = data[key] if isinstance(data[key], list) else []
                                    break

                        if filings:
                            logger.debug(f"    ✓ Found {len(filings)} records")
                            return filings

                except Exception as e:
                    logger.debug(f"    Error: {str(e)[:50]}")

            logger.debug(f"  No data found from API endpoints")
            return []

        except Exception as e:
            logger.error(f"Error searching filings: {str(e)[:100]}")
            return []

    def get_all_filings(self, year: int, limit: int = 5000) -> List[Dict]:
        """Get all filings for a year."""
        logger.info(f"Fetching Senate eFD filings for {year}...")

        try:
            # Try to get all filings
            params = {
                "year": year,
                "limit": limit,
            }

            urls_to_try = [
                f"{self.API_URL}/filings",
                f"{self.API_URL}/search",
                "https://efdsearch.senate.gov/api/filing/search",
            ]

            for url in urls_to_try:
                try:
                    logger.debug(f"  Trying {url}...")
                    r = self.session.get(url, params=params, timeout=15)

                    if r.status_code == 200:
                        data = r.json()

                        filings = []
                        if isinstance(data, list):
                            filings = data
                        elif isinstance(data, dict):
                            for key in ["filings", "results", "data", "hits", ""]:
                                if key and key in data:
                                    filings = data[key]
                                    break
                                elif not key:  # Try whole dict as filings
                                    filings = data

                        if filings and len(filings) > 0:
                            logger.info(f"  ✓ Got {len(filings)} filings from {url}")
                            return filings if isinstance(filings, list) else list(filings.values()) if isinstance(filings, dict) else []

                except Exception as e:
                    logger.debug(f"    Failed: {str(e)[:50]}")

            logger.warning(f"  No filings found from any API endpoint")
            return []

        except Exception as e:
            logger.error(f"Error getting all filings: {str(e)[:100]}")
            return []

    def ingest_year(self, year: int, db_session) -> Dict:
        """Ingest all Senate filings for a year."""
        logger.info(f"\n{'='*70}")
        logger.info(f"Ingesting Senate eFD for {year}")
        logger.info(f"{'='*70}")

        filings = self.get_all_filings(year)

        if not filings:
            logger.warning(f"  No filings retrieved for {year}")
            return {"imported": 0, "errors": 1, "skipped": 0}

        imported = 0
        skipped = 0
        errors = 0

        for filing in filings:
            try:
                # Parse filing data (structure varies, handle flexibly)
                if isinstance(filing, dict):
                    last_name = filing.get("lastName") or filing.get("last_name") or ""
                    first_name = filing.get("firstName") or filing.get("first_name") or ""
                    filing_date_str = filing.get("filingDate") or filing.get("filing_date") or filing.get("date") or ""
                    filing_type = filing.get("filingType") or filing.get("filing_type") or "FD"
                    doc_id = filing.get("filingId") or filing.get("filing_id") or filing.get("id") or ""

                    if not last_name or not doc_id:
                        skipped += 1
                        continue

                    # Find member
                    member = db_session.query(Member).filter(
                        (Member.last_name == last_name.strip()) &
                        (Member.first_name == first_name.strip()) &
                        (Member.chamber == Chamber.SENATE)
                    ).first()

                    if not member:
                        logger.debug(f"    Member not found: {first_name} {last_name}")
                        skipped += 1
                        continue

                    # Check for duplicate
                    existing = db_session.query(Disclosure).filter(
                        Disclosure.document_id == str(doc_id)
                    ).first()

                    if existing:
                        skipped += 1
                        continue

                    # Parse filing date
                    try:
                        if filing_date_str:
                            filing_date = datetime.fromisoformat(filing_date_str.replace("Z", "+00:00"))
                        else:
                            filing_date = None
                    except Exception:
                        filing_date = None

                    # Create disclosure
                    disclosure = Disclosure(
                        member_id=member.id,
                        filing_year=year,
                        filing_type=filing_type,
                        filing_date=choose_filing_date(filing_date, year),
                        document_id=str(doc_id),
                        document_url=f"https://efdsearch.senate.gov/filing/{doc_id}/",
                        is_ptr=False,
                        parsed=False
                    )

                    db_session.add(disclosure)
                    imported += 1

                else:
                    skipped += 1

            except Exception as e:
                logger.error(f"    Error processing filing: {str(e)[:50]}")
                errors += 1

        db_session.commit()

        logger.info(f"\nResults for {year}:")
        logger.info(f"  Imported: {imported}")
        logger.info(f"  Skipped: {skipped}")
        logger.info(f"  Errors: {errors}")

        return {"imported": imported, "errors": errors, "skipped": skipped}

    def ingest_all(self, years: List[int]):
        """Ingest Senate eFD for multiple years."""
        db = SessionLocal()

        try:
            logger.info(f"\nStarting Senate eFD ingestion for {len(years)} years...")

            total_imported = 0
            total_errors = 0
            total_skipped = 0

            for year in sorted(years, reverse=True):  # Start with recent
                result = self.ingest_year(year, db)
                total_imported += result["imported"]
                total_errors += result["errors"]
                total_skipped += result["skipped"]

            logger.info(f"\n{'='*70}")
            logger.info(f"FINAL SUMMARY - Senate eFD (All Years)")
            logger.info(f"{'='*70}")
            logger.info(f"Total Imported: {total_imported}")
            logger.info(f"Total Skipped: {total_skipped}")
            logger.info(f"Total Errors: {total_errors}")
            logger.info(f"{'='*70}\n")

        finally:
            db.close()


def main():
    """Run Senate eFD ingestion."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    scraper = SenateEFDAPI()

    # Ingest recent years
    years_to_ingest = [2024, 2023, 2022, 2021]
    scraper.ingest_all(years_to_ingest)


if __name__ == "__main__":
    main()

