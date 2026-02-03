"""
FREE Data Ingestion Orchestrator
Combines all free sources: House Clerk, Senate eFD, Wayback Machine
"""
import logging
import sys
import asyncio
from typing import Dict
from datetime import datetime

logger = logging.getLogger(__name__)

class FreeIngestorAll:
    """Orchestrate all FREE FD data ingestion sources."""

    def __init__(self):
        self.results = {}

    def ingest_house_clerk(self) -> Dict:
        """Ingest House Clerk FD data (2010-2024 available)."""
        logger.info(f"\n{'='*70}")
        logger.info("PHASE 1: House Clerk FD XML (2010-2024)")
        logger.info(f"{'='*70}\n")

        try:
            from src.ingestion.house_clerk_historical import HouseClerkHistorical
            ingester = HouseClerkHistorical()

            # Get available years
            available_years = ingester.get_available_years()

            if not available_years:
                logger.warning("No House Clerk years available!")
                return {"imported": 0, "errors": 1, "source": "House Clerk"}

            # Ingest all years
            ingester.ingest_all(years=available_years)

            logger.info(f"\n✓ House Clerk ingestion complete")
            return {"imported": "check_db", "source": "House Clerk", "years": available_years}

        except Exception as e:
            logger.error(f"✗ House Clerk ingestion failed: {str(e)}")
            return {"imported": 0, "errors": 1, "source": "House Clerk"}

    async def ingest_senate_efd(self) -> Dict:
        """Ingest Senate eFD data using Playwright scraper."""
        logger.info(f"\n{'='*70}")
        logger.info("PHASE 2: Senate eFD Database (2012-2024)")
        logger.info(f"{'='*70}\n")

        # Check if Playwright is installed
        try:
            import playwright
        except ImportError:
            logger.warning("⚠️ Playwright not installed!")
            logger.warning("   Install with: pip install playwright")
            logger.warning("   Then run: playwright install")
            logger.warning("   Skipping Senate eFD for now...\n")
            return {"imported": 0, "errors": 1, "source": "Senate eFD", "reason": "Playwright not installed"}

        try:
            from src.ingestion.senate_efd_scraper import SenateEFDScraper
            scraper = SenateEFDScraper()

            result = await scraper.scrape_all_years(start_year=2024, end_year=2024)

            logger.info(f"\n✓ Senate eFD ingestion complete")
            return {"imported": result.get("imported", 0), "source": "Senate eFD"}

        except Exception as e:
            logger.error(f"✗ Senate eFD ingestion failed: {str(e)}")
            return {"imported": 0, "errors": 1, "source": "Senate eFD"}

    def ingest_wayback_machine(self) -> Dict:
        """Ingest historical data from Wayback Machine (pre-2010)."""
        logger.info(f"\n{'='*70}")
        logger.info("PHASE 3: Wayback Machine (Pre-2010)")
        logger.info(f"{'='*70}\n")

        try:
            from src.ingestion.wayback_machine_scraper import WaybackMachineScraper
            scraper = WaybackMachineScraper()

            result = scraper.scrape_historical_period(start_year=2004, end_year=2009)

            logger.info(f"\n✓ Wayback Machine ingestion complete")
            return result

        except Exception as e:
            logger.error(f"✗ Wayback Machine ingestion failed: {str(e)}")
            return {"imported": 0, "errors": 1, "source": "Wayback Machine"}

    def run_all(self, include_senate: bool = False, include_wayback: bool = False):
        """Run all FREE ingestion phases."""
        logger.info(f"\n{'*'*70}")
        logger.info(f"  FREE FD DATA INGESTION ORCHESTRATOR")
        logger.info(f"  Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'*'*70}\n")

        # PHASE 1: House Clerk (always run - most reliable)
        result1 = self.ingest_house_clerk()
        self.results["House Clerk"] = result1

        # PHASE 2: Senate eFD (optional - requires Playwright)
        if include_senate:
            logger.info("\nOptional: Install Playwright first if you want Senate eFD")
            logger.info("  pip install playwright")
            logger.info("  playwright install\n")
            result2 = asyncio.run(self.ingest_senate_efd())
            self.results["Senate eFD"] = result2
        else:
            logger.info("\n⏭️  Skipping Senate eFD (optional)")
            logger.info("  Will implement with Playwright next\n")

        # PHASE 3: Wayback Machine (optional - slow)
        if include_wayback:
            logger.info("⏳ Note: Wayback Machine is slow, be patient...\n")
            result3 = self.ingest_wayback_machine()
            self.results["Wayback Machine"] = result3
        else:
            logger.info("⏭️  Skipping Wayback Machine (optional)")
            logger.info("  Use for pre-2010 data if needed\n")

        # Summary
        self.print_summary()

    def print_summary(self):
        """Print final summary."""
        logger.info(f"\n{'*'*70}")
        logger.info(f"  INGESTION COMPLETE")
        logger.info(f"{'*'*70}\n")

        total_imported = 0
        total_errors = 0

        for source, result in self.results.items():
            imported = result.get("imported", 0)
            errors = result.get("errors", 0)

            if isinstance(imported, int):
                total_imported += imported

            if isinstance(errors, int):
                total_errors += errors

            status = "✓" if errors == 0 else "⚠"
            logger.info(f"{status} {source}")
            if isinstance(imported, int):
                logger.info(f"    Imported: {imported} records")
            if errors > 0:
                logger.info(f"    Errors: {errors}")

            years = result.get("years")
            if years:
                logger.info(f"    Years: {years}")

        logger.info(f"\n{'='*70}")
        logger.info(f"Total Records Imported: {total_imported}")
        logger.info(f"Total Errors: {total_errors}")
        logger.info(f"{'='*70}\n")

        # Next steps
        logger.info("NEXT STEPS:")
        logger.info("  1. Verify data was imported: python -m src.cli list-disclosures")
        logger.info("  2. Run anomaly detection: python -m src.cli analyze")
        logger.info("  3. View dashboard: python -m src.cli serve --port 8001\n")

        logger.info("OPTIONAL FREE ADDITIONS:")
        logger.info("  • Senate eFD: pip install playwright && playwright install")
        logger.info("  • Wayback Machine: python scripts/runfree_ingestion.py --wayback")
        logger.info("  • SEC EDGAR: Implement supplementary verification layer\n")


def main():
    """Run all free ingestion."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    import argparse
    parser = argparse.ArgumentParser(description="FREE FD Data Ingestion")
    parser.add_argument("--senate", action="store_true", help="Include Senate eFD (requires Playwright)")
    parser.add_argument("--wayback", action="store_true", help="Include Wayback Machine (slow)")
    parser.add_argument("--all", action="store_true", help="Include all optional sources")
    args = parser.parse_args()

    orchestrator = FreeIngestorAll()
    orchestrator.run_all(
        include_senate=args.senate or args.all,
        include_wayback=args.wayback or args.all
    )


if __name__ == "__main__":
    main()

