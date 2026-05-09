"""
Final verification test for retired members with anomalies.
Run this AFTER restarting the server.
"""

import requests

print("=" * 80)
print("FINAL VERIFICATION TEST")
print("=" * 80)

# Test 1: Database query
print("\n1. DATABASE CHECK (direct query)")
from sqlalchemy import exists

from src.db import Anomaly, Member, get_db_session

db = next(get_db_session())
retired_query = db.query(Member).filter(
    Member.in_office == False, exists().where(Anomaly.member_id == Member.id)
)
db_retired_count = retired_query.count()
print(f"   ✓ Retired members with anomalies in DB: {db_retired_count}")

# Get some examples
examples = retired_query.limit(5).all()
for m in examples:
    anomaly_count = db.query(Anomaly).filter(Anomaly.member_id == m.id).count()
    print(
        f"     - {m.first_name} {m.last_name} ({m.party.value}-{m.state}): {anomaly_count} anomalies"
    )
db.close()

# Test 2: API endpoint
print("\n2. API ENDPOINT CHECK")
try:
    response = requests.get(
        "http://localhost:8000/api/members?has_anomalies=true&page_size=500", timeout=5
    )
    if response.status_code == 200:
        data = response.json()
        total = data["total"]
        members = data["members"]
        retired_api = [m for m in members if m["in_office"] == False]
        active_api = [m for m in members if m["in_office"] == True]

        print(f"   ✓ Total members with anomalies: {total}")
        print(f"   ✓ Returned in this page: {len(members)}")
        print(f"   ✓ Active members: {len(active_api)}")
        print(f"   ✓ Retired members: {len(retired_api)}")

        print("\n   Sample retired members from API:")
        for m in retired_api[:5]:
            print(
                f"     - {m['first_name']} {m['last_name']} ({m['party']}-{m['state']}): {m['anomaly_count']} anomalies, in_office={m['in_office']}"
            )

        if len(retired_api) == 0:
            print("\n   ❌ ERROR: API returned 0 retired members!")
            print("   Check if server was restarted with the new code.")
        elif len(retired_api) != db_retired_count:
            print(f"\n   ⚠️  WARNING: API returned {len(retired_api)} but DB has {db_retired_count}")
            print("   You may need to increase page_size or fetch multiple pages.")
        else:
            print(f"\n   ✅ SUCCESS: API matches database ({len(retired_api)} retired members)")
    else:
        print(f"   ❌ API Error: Status {response.status_code}")
except requests.exceptions.ConnectionError:
    print("   ❌ ERROR: Could not connect to server at http://localhost:8000")
    print("   Please start the server with: python start_server.py")
except Exception as e:
    print(f"   ❌ ERROR: {e}")

# Test 3: Instructions
print("\n" + "=" * 80)
print("NEXT STEPS")
print("=" * 80)
print("""
1. If server is not running:
   cd C:\\Users\\prend\\IdeaProjects\\honest-congress
   python start_server.py

2. Open browser to: http://localhost:8000

3. Open browser console (F12)

4. Select "Retired" from Status dropdown

5. Check console logs for:
   - "Loaded members from API: X"
   - "Active: X, Retired: Y"
   - "Filter in_office: retired"
   - "After filter: X members"

6. The left sidebar should show retired members with "(Retired)" after their names.

Expected result: You should see 55 retired members including:
  - Pat Roberts (R-KS)
  - Kelly Loeffler (R-GA)
  - Claire McCaskill (D-MO)
  - Dean Heller (R-NV)
  - James Inhofe (R-OK)
""")
print("=" * 80)
