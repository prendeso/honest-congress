"""Test script to find House Clerk API endpoints."""
import requests
import re

print("Fetching House Clerk page...")
r = requests.get('https://disclosures-clerk.house.gov/FinancialDisclosure',
                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
print(f"Status: {r.status_code}")

# Find API-like URLs
patterns = [
    r'["\'](/[^"\']*(?:api|search|disclosure)[^"\']*)["\']',
    r'url:\s*["\']([^"\']+)["\']',
    r'href="([^"]*(?:ptr|disclosure)[^"]*)"',
]

all_urls = set()
for pattern in patterns:
    urls = re.findall(pattern, r.text, re.IGNORECASE)
    all_urls.update(urls)

print(f"\nPotential API/search URLs found ({len(all_urls)}):")
for url in sorted(all_urls)[:20]:
    print(f"  {url}")

# Look for script sources
scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', r.text)
print(f"\nScript sources ({len(scripts)}):")
for s in scripts[:10]:
    print(f"  {s}")

# Try the PublicDisclosure endpoints
print("\n=== Testing potential endpoints ===")
test_urls = [
    '/PublicDisclosure/FinancialDisclosure/Search',
    '/PublicDisclosure/FinancialDisclosure/ViewSearch',
    '/FinancialDisclosure/Search',
    '/api/Search',
    '/api/Disclosure',
]

for url in test_urls:
    full_url = f"https://disclosures-clerk.house.gov{url}"
    try:
        r2 = requests.get(full_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        print(f"  {url}: {r2.status_code}")
    except Exception as e:
        print(f"  {url}: Error - {e}")

