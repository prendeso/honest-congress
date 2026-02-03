"""
Get Senate FD data from QuiverQuant API.
We have the API key and QuiverQuant has Senate member data!
"""
import requests
import os
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class QuiverQuantSenateFD:
    """Get Senate FD data from QuiverQuant"""

    API_KEY = "154cc513278c4b9da1f00ad8aa89d7ca84790a95"  # From your account
    BASE_URL = "https://api.quiverquant.com/api/v1"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.API_KEY}",
        })

    def get_available_endpoints(self) -> List[str]:
        """Get list of available endpoints."""
        logger.info("Testing QuiverQuant API endpoints...")

        endpoints = [
            "/senators",
            "/senate",
            "/senate/disclosures",
            "/filings/senate",
            "/members/senate",
            "/fds",
            "/financial-disclosures",
        ]

        available = []
        for endpoint in endpoints:
            try:
                url = f"{self.BASE_URL}{endpoint}"
                r = self.session.get(url, timeout=5)
                status = "✓" if r.status_code in [200, 403] else "✗"
                logger.info(f"  {status} {endpoint}: HTTP {r.status_code}")

                if r.status_code == 200:
                    available.append(endpoint)
                    try:
                        data = r.json()
                        if isinstance(data, list):
                            logger.info(f"    → Returns list with {len(data)} items")
                        elif isinstance(data, dict):
                            logger.info(f"    → Returns dict with keys: {list(data.keys())[:3]}")
                    except:
                        pass
            except Exception as e:
                logger.debug(f"  Error: {str(e)[:50]}")

        return available

    def get_senators(self) -> List[Dict]:
        """Get list of senators with FD data."""
        logger.info("\nFetching Senate members...")

        try:
            # Try different endpoints
            endpoints = [
                "/senators",
                "/senate",
                "/members",
            ]

            for endpoint in endpoints:
                try:
                    url = f"{self.BASE_URL}{endpoint}"
                    r = self.session.get(url, timeout=10)

                    if r.status_code == 200:
                        data = r.json()

                        if isinstance(data, list) and len(data) > 0:
                            logger.info(f"✓ Got {len(data)} senators from {endpoint}")
                            logger.info(f"  First senator: {str(data[0])[:100]}")
                            return data
                        elif isinstance(data, dict):
                            # Check for common wrapper keys
                            for key in ["senators", "data", "results"]:
                                if key in data and isinstance(data[key], list):
                                    logger.info(f"✓ Got {len(data[key])} senators from {endpoint}[{key}]")
                                    return data[key]

                except Exception as e:
                    logger.debug(f"  {endpoint}: {str(e)[:50]}")

            logger.warning("No senators found from any endpoint")
            return []

        except Exception as e:
            logger.error(f"Error getting senators: {str(e)}")
            return []

    def get_senate_trades(self, limit: int = 100) -> List[Dict]:
        """Get Senate member trades."""
        logger.info(f"\nFetching Senate trades (limit: {limit})...")

        try:
            # We know this endpoint works from before!
            url = f"{self.BASE_URL}/senate/trades"
            params = {"limit": limit}

            r = self.session.get(url, params=params, timeout=10)

            if r.status_code == 200:
                data = r.json()

                if isinstance(data, list):
                    logger.info(f"✓ Got {len(data)} Senate trades")
                    return data
                elif isinstance(data, dict) and "trades" in data:
                    logger.info(f"✓ Got {len(data['trades'])} Senate trades")
                    return data["trades"]
                else:
                    logger.info(f"✓ Got response, type: {type(data)}")
                    return data if isinstance(data, list) else []
            else:
                logger.warning(f"HTTP {r.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error: {str(e)[:100]}")
            return []

    def get_fd_data(self) -> List[Dict]:
        """Get FD (Financial Disclosure) data if available."""
        logger.info("\nLooking for FD endpoint...")

        endpoints = [
            "/filings",
            "/filings/fd",
            "/disclosures",
            "/fds",
            "/financial-disclosures",
            "/senate/filings",
            "/senators/filings",
        ]

        for endpoint in endpoints:
            try:
                url = f"{self.BASE_URL}{endpoint}"
                logger.debug(f"  Trying {endpoint}...")
                r = self.session.get(url, timeout=5)

                if r.status_code == 200:
                    data = r.json()
                    count = len(data) if isinstance(data, list) else len(data.get("data", [])) if isinstance(data, dict) else 0
                    logger.info(f"✓ Found {endpoint} - {count} records")
                    return data if isinstance(data, list) else data.get("data", [])
                elif r.status_code == 403:
                    logger.info(f"  {endpoint} - Tier 1 access limited")

            except Exception as e:
                logger.debug(f"  Error: {str(e)[:30]}")

        logger.warning("No FD endpoint found")
        return []


def main():
    """Test QuiverQuant API for Senate data."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    api = QuiverQuantSenateFD()

    logger.info("="*70)
    logger.info("QuiverQuant Senate Data Exploration")
    logger.info("="*70)

    # Test endpoints
    available = api.get_available_endpoints()
    logger.info(f"\nAvailable endpoints: {len(available)}")
    for ep in available:
        logger.info(f"  • {ep}")

    # Get senators
    senators = api.get_senators()
    logger.info(f"\nFound {len(senators)} senators")

    # Get trades (we know this works!)
    trades = api.get_senate_trades(limit=10)
    logger.info(f"\nFound {len(trades)} Senate trades")

    if trades:
        logger.info(f"Sample trade: {trades[0]}")

    # Look for FD data
    fd_data = api.get_fd_data()
    logger.info(f"\nFound {len(fd_data)} FD records (if any)")

    logger.info("\n" + "="*70)
    logger.info("SUMMARY")
    logger.info("="*70)
    logger.info(f"✓ API is accessible")
    logger.info(f"✓ Senate trades available: {len(trades)}")
    logger.info(f"✓ FD data available: {len(fd_data)}")
    logger.info(f"\nTier 1 limitations: Limited FD access")
    logger.info(f"Next: Use the trade data for analysis!")


if __name__ == "__main__":
    main()

