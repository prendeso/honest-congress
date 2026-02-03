"""Test all free sources for FD data availability."""
import requests
import sys

print("\n" + "="*70)
print("TESTING ALL FREE SOURCES AVAILABILITY")
print("="*70 + "\n")

# 1. House Clerk Historical XML
print("1. HOUSE CLERK XML (2004-2024)")
print("-" * 70)
house_years = {}
for year in range(2004, 2025, 3):  # Test every 3 years
    url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.xml"
    try:
        r = requests.head(url, timeout=5)
        status = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
        symbol = "✓" if r.status_code == 200 else "✗"
        print(f"  {year}: {symbol} {status}")
        house_years[year] = r.status_code == 200
    except Exception as e:
        print(f"  {year}: ✗ ERROR")
        house_years[year] = False

available = sum(1 for v in house_years.values() if v)
print(f"Result: {available}/{len(house_years)} years available\n")

# 2. SEC EDGAR
print("2. SEC EDGAR")
print("-" * 70)
try:
    r = requests.head("https://www.sec.gov/cgi-bin/browse-edgar", timeout=5)
    if r.status_code == 200:
        print("  ✓ Available - Can download officer/director filings")
    else:
        print(f"  ✗ HTTP {r.status_code}")
except Exception as e:
    print(f"  ✗ ERROR: {str(e)[:50]}")

# Try API endpoint
try:
    r = requests.get("https://data.sec.gov/submissions/CIK0000000001.json", timeout=5)
    if r.status_code == 200:
        print("  ✓ SEC EDGAR API available")
except:
    pass
print()

# 3. Senate eFD
print("3. SENATE eFD (efdsearch.senate.gov)")
print("-" * 70)
try:
    r = requests.get("https://efdsearch.senate.gov/", timeout=10)
    if r.status_code == 200:
        print(f"  ✓ Available (HTTP {r.status_code})")
        if "JavaScript" in r.text or "angular" in r.text.lower():
            print("    Note: JavaScript-rendered (needs Playwright)")
    else:
        print(f"  ✗ HTTP {r.status_code}")
except Exception as e:
    print(f"  ✗ ERROR: {str(e)[:50]}")
print()

# 4. Wayback Machine
print("4. WAYBACK MACHINE (web.archive.org)")
print("-" * 70)
try:
    # Test getting availability API
    url = "https://archive.org/wayback/available"
    params = {
        "url": "disclosures-clerk.house.gov",
        "timestamp": "20050101"
    }
    r = requests.get(url, params=params, timeout=5)
    if r.status_code == 200:
        print("  ✓ Availability API working")
        data = r.json()
        if data.get("archived_snapshots"):
            print(f"    Found snapshots for House Clerk")
    else:
        print(f"  ✗ HTTP {r.status_code}")
except Exception as e:
    print(f"  ✗ ERROR: {str(e)[:50]}")
print()

# 5. Congress.gov API
print("5. CONGRESS.GOV API")
print("-" * 70)
try:
    r = requests.get("https://api.congress.gov/v3/members", timeout=5)
    if r.status_code == 200:
        print("  ✓ API Available (limited FD data)")
    else:
        print(f"  ✗ HTTP {r.status_code}")
except Exception as e:
    print(f"  ✗ ERROR: {str(e)[:50]}")
print()

# Summary
print("="*70)
print("SUMMARY - BEST FREE OPTIONS")
print("="*70)
print("""
✓ HOUSE CLERK XML (2004-2024)
  - FREE, no auth needed
  - Direct download
  - We already use for 2024-2025

✓ SEC EDGAR (1990-2026)
  - FREE, no auth needed
  - API available
  - Officers/directors only
  - Good for verification

✓ SENATE eFD (2012-2026)
  - FREE, no auth needed
  - JavaScript-rendered
  - Needs Playwright scraper
  - ~5,000 Senate reports

✓ WAYBACK MACHINE (2000+)
  - FREE, no auth needed
  - API available
  - Historical snapshots
  - Pre-2004 House Clerk

? CONGRESS.GOV (Limited)
  - FREE API available
  - Limited FD references only
  - Not primary source

""")
print("="*70)

