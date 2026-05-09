"""
Scrape historical House Clerk data from Wayback Machine.
Useful for years before 2010 when House Clerk XML not available.
"""

import logging
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


class WaybackMachineScraper:
    """Retrieve historical House Clerk snapshots from web.archive.org"""

    AVAILABILITY_API = "https://archive.org/wayback/available"
    CAPTURE_API = "https://web.archive.org/web"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "HonestCongress/1.0 (Archive Crawler)"})

    def get_snapshots(self, target_url: str, start_year: int, end_year: int) -> List[str]:
        """Get list of available snapshots for a URL in a date range."""
        logger.info(f"Querying Wayback Machine for snapshots ({start_year}-{end_year})...")

        snapshots = []

        try:
            # Use availability API to get snapshots
            params = {"url": target_url, "matchType": "prefix", "output": "json"}

            r = self.session.get(self.AVAILABILITY_API, params=params, timeout=30)
            r.raise_for_status()

            data = r.json()

            if data.get("results"):
                for result in data["results"]:
                    timestamp = result.get("timestamp", "")
                    year = int(timestamp[:4]) if len(timestamp) >= 4 else 0

                    if start_year <= year <= end_year:
                        snapshots.append(timestamp)
                        logger.debug(f"  Found snapshot: {timestamp}")

            logger.info(f"✓ Found {len(snapshots)} snapshots")
            return snapshots

        except Exception as e:
            logger.error(f"✗ Error querying Wayback: {str(e)}")
            return []

    def download_snapshot(self, snapshot_id: str, target_url: str) -> str | None:
        """Download a specific snapshot from Wayback Machine."""
        url = f"{self.CAPTURE_API}/{snapshot_id}/{target_url}"

        try:
            logger.debug(f"Downloading snapshot {snapshot_id}...")
            r = self.session.get(url, timeout=30)

            if r.status_code == 200:
                logger.debug(f"  ✓ Downloaded ({len(r.content)} bytes)")
                return r.text
            else:
                logger.debug(f"  ✗ HTTP {r.status_code}")
                return None

        except Exception as e:
            logger.debug(f"  ✗ Error: {str(e)[:50]}")
            return None

    def parse_html_table(self, html: str) -> List[Dict]:
        """Parse HTML table from House Clerk snapshot."""
        records = []

        try:
            # This is simplified - real implementation would use BeautifulSoup
            # to properly parse HTML tables

            # Look for common patterns in House Clerk HTML
            if "<table" in html.lower():
                logger.debug("  Found table in HTML")
                # In production, use:
                # from bs4 import BeautifulSoup
                # soup = BeautifulSoup(html, 'html.parser')
                # for row in soup.find_all('tr'):
                #     cells = row.find_all('td')
                #     ...

            return records

        except Exception as e:
            logger.error(f"  Error parsing HTML: {str(e)[:50]}")
            return []

    def scrape_historical_period(self, start_year: int = 2004, end_year: int = 2009):
        """Scrape House Clerk data for years before 2010."""
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Scraping Wayback Machine for {start_year}-{end_year}")
        logger.info(f"{'=' * 70}\n")

        target_url = "disclosures-clerk.house.gov"

        # Get available snapshots
        snapshots = self.get_snapshots(target_url, start_year, end_year)

        if not snapshots:
            logger.warning("No snapshots found for this period")
            return {"imported": 0, "errors": 1}

        total_imported = 0
        total_errors = 0

        # Process each snapshot (limit to recent ones per year to avoid too many)
        for snapshot_id in sorted(snapshots, reverse=True)[:50]:  # Limit to 50 snapshots
            try:
                html = self.download_snapshot(snapshot_id, target_url)
                if html:
                    records = self.parse_html_table(html)
                    total_imported += len(records)
                else:
                    total_errors += 1

            except Exception as e:
                logger.error(f"Error processing snapshot {snapshot_id}: {str(e)[:50]}")
                total_errors += 1

        logger.info(f"\n{'=' * 70}")
        logger.info("Wayback Machine Scraping Complete")
        logger.info(f"Imported: {total_imported}")
        logger.info(f"Errors: {total_errors}")
        logger.info(f"{'=' * 70}\n")

        return {"imported": total_imported, "errors": total_errors}


def main():
    """Run Wayback Machine scraping."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    scraper = WaybackMachineScraper()
    scraper.scrape_historical_period(start_year=2004, end_year=2009)


if __name__ == "__main__":
    main()
