"""
Scrape Senate eFD database using Playwright.
Handles JavaScript-rendered pages and anti-bot protection.
Focuses on extracting individual filings with proper data parsing.
"""
import logging
import asyncio
from typing import List, Dict, Optional
from datetime import datetime
from src.db.database import SessionLocal
from src.db.models import Disclosure, Member, Chamber

logger = logging.getLogger(__name__)

class SenateEFDScraper:
    """Scrape Senate Financial Disclosures from efdsearch.senate.gov"""

    BASE_URL = "https://efdsearch.senate.gov"

    def __init__(self):
        self.playwright = None
        self.browser = None

    async def init(self):
        """Initialize Playwright browser."""
        try:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()

            # Launch browser with anti-detection measures
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ]
            )

            logger.info("✓ Playwright browser initialized")
            return True
        except ImportError:
            logger.error("✗ Playwright not installed. Install with: pip install playwright")
            logger.error("   Then run: playwright install")
            return False
        except Exception as e:
            logger.error(f"✗ Failed to initialize Playwright: {str(e)}")
            return False

    async def close(self):
        """Close Playwright browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("✓ Playwright browser closed")

    async def search_senator_filings(self, senator_name: str, year: int) -> List[Dict]:
        """Search for filings for a specific senator in a specific year."""
        filings = []

        try:
            page = await self.browser.new_page()

            # Set user agent to avoid detection
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            })

            logger.debug(f"  Loading search page...")

            # Navigate to the search/index page
            try:
                await page.goto(f"{self.BASE_URL}/search/", wait_until="networkidle", timeout=30000)
            except Exception as e:
                logger.warning(f"  Navigation timeout, continuing: {str(e)[:50]}")
                pass

            # Wait for page to load
            await asyncio.sleep(2)

            # Try to find the search input
            try:
                # Look for senator name input
                name_input = await page.query_selector("input[name='name'], input[placeholder*='name'], input[placeholder*='Name']")

                if name_input:
                    logger.debug(f"  Found name input, entering '{senator_name}'...")
                    await name_input.fill(senator_name)
                    await asyncio.sleep(1)

                # Try year input
                year_input = await page.query_selector("input[name='year'], input[placeholder*='year'], input[placeholder*='Year']")
                if year_input:
                    logger.debug(f"  Found year input, entering {year}...")
                    await year_input.fill(str(year))
                    await asyncio.sleep(1)

                # Try to find and click search button
                search_btn = await page.query_selector("button[type='submit'], button:has-text('Search')")
                if search_btn:
                    logger.debug(f"  Clicking search button...")
                    await search_btn.click()
                    await asyncio.sleep(3)
                else:
                    logger.debug(f"  No search button found, trying Enter key...")
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)

            except Exception as e:
                logger.debug(f"  Form interaction failed: {str(e)[:50]}")

            # Try to extract results from table or list
            try:
                # Look for table rows with filing data
                rows = await page.query_selector_all("table tbody tr, div[class*='filing'], div[class*='row']")
                logger.debug(f"  Found {len(rows)} potential result rows")

                for i, row in enumerate(rows[:20]):  # Limit to first 20 results
                    try:
                        # Try different selectors for filing data
                        cells = await row.query_selector_all("td, div[class*='cell'], span")

                        if len(cells) >= 2:
                            text_content = []
                            for cell in cells[:5]:  # Get first 5 cells
                                try:
                                    text = await cell.text_content()
                                    if text and text.strip():
                                        text_content.append(text.strip())
                                except:
                                    pass

                            if text_content:
                                filing_data = {
                                    "name": senator_name,
                                    "year": year,
                                    "filing_date": None,
                                    "data": " | ".join(text_content)
                                }
                                filings.append(filing_data)
                                logger.debug(f"    Filing {i+1}: {' | '.join(text_content[:2])}")
                    except Exception as e:
                        logger.debug(f"    Error parsing row {i}: {str(e)[:30]}")

            except Exception as e:
                logger.debug(f"  Error extracting results: {str(e)[:50]}")

            await page.close()

            if filings:
                logger.info(f"  ✓ Found {len(filings)} filings for {senator_name} ({year})")
            else:
                logger.debug(f"  No filings found for {senator_name} ({year})")

            return filings

        except Exception as e:
            logger.error(f"  ✗ Error searching filings: {str(e)[:100]}")
            try:
                await page.close()
            except:
                pass
            return []

    async def get_all_senators(self, db_session) -> List[str]:
        """Get list of all current Senate members from database."""
        senators = db_session.query(Member.first_name, Member.last_name).filter(
            Member.chamber == Chamber.SENATE,
            Member.in_office == True
        ).all()

        return [f"{first} {last}" for first, last in senators]

    async def scrape_years(self, years: List[int], max_senators: Optional[int] = None) -> Dict:
        """Scrape Senate FD for multiple years."""
        if not await self.init():
            return {"imported": 0, "errors": 1, "tried": 0}

        db = SessionLocal()

        try:
            # Get list of senators
            senators = await self.get_all_senators(db)
            logger.info(f"Found {len(senators)} current senators")

            if max_senators:
                senators = senators[:max_senators]
                logger.info(f"Limiting to {max_senators} senators for testing")

            total_filings = 0
            total_errors = 0
            attempts = 0

            logger.info(f"\n{'='*70}")
            logger.info(f"Scraping Senate eFD for {len(years)} years")
            logger.info(f"{'='*70}\n")

            for year in sorted(years, reverse=True):  # Start with most recent
                logger.info(f"Year {year}:")

                for senator in senators:
                    try:
                        attempts += 1
                        filings = await self.search_senator_filings(senator, year)
                        total_filings += len(filings)

                        # Be respectful with rate limiting
                        await asyncio.sleep(0.5)

                    except Exception as e:
                        logger.error(f"  Error for {senator} ({year}): {str(e)[:50]}")
                        total_errors += 1

            return {
                "tried": attempts,
                "imported": total_filings,
                "errors": total_errors,
                "years": len(years)
            }

        finally:
            db.close()
            await self.close()


async def main():
    """Run Senate eFD scraping."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    scraper = SenateEFDScraper()

    # Test with just 2024 and a few senators
    result = await scraper.scrape_years(
        years=[2024, 2023],
        max_senators=5  # Test with 5 senators first
    )

    logger.info(f"\n{'='*70}")
    logger.info(f"Senate eFD Scraping Complete")
    logger.info(f"Attempts: {result['tried']}")
    logger.info(f"Filings Found: {result['imported']}")
    logger.info(f"Errors: {result['errors']}")
    logger.info(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
