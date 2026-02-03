"""Test the actual search endpoint."""
import requests
import json

BASE = "https://disclosures-clerk.house.gov"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/html, */*',
    'Content-Type': 'application/x-www-form-urlencoded',
}

# Test ViewSearch with POST
print("=== Testing ViewSearch POST ===")
search_url = f"{BASE}/PublicDisclosure/FinancialDisclosure/ViewSearch"

# Try different parameter combinations
params_list = [
    # Form data for search
    {"FilingYear": "2024", "State": "", "District": "", "ReportType": ""},
    {"FilingYear": "2024", "FilingType": "P"},  # P = PTR
    {"year": "2024"},
    {"searchYear": "2024"},
]

for params in params_list:
    print(f"\nTrying params: {params}")
    try:
        r = requests.post(search_url, data=params, headers=headers, timeout=15)
        print(f"  Status: {r.status_code}")
        print(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")

        # Check if it's JSON
        if 'json' in r.headers.get('Content-Type', ''):
            data = r.json()
            if isinstance(data, list):
                print(f"  Records: {len(data)}")
                if data:
                    print(f"  Sample keys: {list(data[0].keys())[:5]}")
            elif isinstance(data, dict):
                print(f"  Keys: {list(data.keys())[:5]}")
        else:
            # Check HTML for table
            if '<table' in r.text.lower():
                print("  Contains table!")
                # Count rows
                import re
                rows = len(re.findall(r'<tr', r.text))
                print(f"  Table rows: ~{rows}")
            print(f"  Content preview: {r.text[:200]}...")
    except Exception as e:
        print(f"  Error: {e}")

# Also try GET with query params
print("\n=== Testing ViewSearch GET ===")
for params in params_list:
    try:
        r = requests.get(search_url, params=params, headers=headers, timeout=15)
        print(f"Params {params}: Status {r.status_code}, Length {len(r.text)}")
    except Exception as e:
        print(f"Error: {e}")

