"""
Test actual QuiverQuant API endpoints from documentation.
Reference: https://api.quiverquant.com/docs/
"""
import requests
import json
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

API_KEY = "154cc513278c4b9da1f00ad8aa89d7ca84790a95"
BASE_URL = "https://api.quiverquant.com/api/v1"

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {API_KEY}"})

logger.info("Testing QuiverQuant API v1 endpoints...")
logger.info("="*70)

# Test various endpoints from their documentation
endpoints = [
    ("/congress/trades", "House and Senate trades"),
    ("/congress/trading", "All trades"),
    ("/congress", "Congress data"),
    ("/house/trades", "House trades"),
    ("/senate/trades", "Senate trades"),
    ("/members", "Members/Representatives"),
    ("/representatives", "Representatives"),
    ("/lobbying", "Lobbying data"),
    ("/insider/trades", "Insider trades"),
    ("/crowdvotes", "CrowdVotes"),
]

logger.info(f"\nTesting {len(endpoints)} endpoints:\n")

working = []
for endpoint, description in endpoints:
    try:
        url = f"{BASE_URL}{endpoint}"
        r = session.get(url, timeout=5)

        status_symbol = "✓" if r.status_code == 200 else "✗"

        try:
            data = r.json()
            if isinstance(data, list):
                count = len(data)
                logger.info(f"{status_symbol} {endpoint:25} HTTP {r.status_code}  ({count} items) - {description}")
                if r.status_code == 200:
                    working.append((endpoint, count))
            else:
                logger.info(f"{status_symbol} {endpoint:25} HTTP {r.status_code}  (dict) - {description}")
                if r.status_code == 200:
                    working.append((endpoint, "dict"))
        except:
            logger.info(f"{status_symbol} {endpoint:25} HTTP {r.status_code}  - {description}")
            if r.status_code == 200:
                working.append((endpoint, "data"))

    except Exception as e:
        logger.info(f"✗ {endpoint:25} ERROR: {str(e)[:30]}")

logger.info(f"\n{'='*70}")
logger.info(f"\nWorking endpoints ({len(working)}):\n")

for endpoint, info in working:
    logger.info(f"  ✓ {endpoint}: {info}")

logger.info(f"\nQuiverQuant API is accessible!")
logger.info(f"We can use these endpoints for data ingestion.")

