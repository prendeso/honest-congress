"""Check anomaly data and verify per-year separation."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), '..', 'honest_congress.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check all large trade anomalies
print("=" * 60)
print("LARGE TRADE ANOMALIES")
print("=" * 60)

cursor.execute("""
    SELECT a.id, a.title, a.disclosure_id, a.transaction_id, 
           m.first_name || ' ' || m.last_name as member_name,
           d.filing_year
    FROM anomalies a
    JOIN members m ON a.member_id = m.id
    LEFT JOIN disclosures d ON a.disclosure_id = d.id
    WHERE a.anomaly_type = 'large_trade'
    ORDER BY member_name, d.filing_year
""")

rows = cursor.fetchall()
print(f"Total large trade anomalies: {len(rows)}\n")

# Group by member
from collections import defaultdict
by_member = defaultdict(list)
for row in rows:
    by_member[row[4]].append(row)

for member, anomalies in sorted(by_member.items()):
    if len(anomalies) > 1:
        print(f"\n{member}: {len(anomalies)} anomalies")
        for a in anomalies:
            print(f"  ID {a[0]}: Year {a[5]} - {a[1][:50]}...")

# Check Nancy Pelosi specifically
print("\n" + "=" * 60)
print("NANCY PELOSI DETAILS")
print("=" * 60)

cursor.execute("""
    SELECT m.id FROM members m WHERE m.last_name = 'Pelosi'
""")
pelosi_id = cursor.fetchone()
if pelosi_id:
    pelosi_id = pelosi_id[0]

    # Get all her transactions
    cursor.execute("""
        SELECT t.id, t.ticker, t.amount_min, t.transaction_type, d.filing_year, d.id as disclosure_id
        FROM transactions t
        JOIN disclosures d ON t.disclosure_id = d.id
        WHERE d.member_id = ?
        ORDER BY d.filing_year
    """, (pelosi_id,))

    txns = cursor.fetchall()
    print(f"Pelosi transactions: {len(txns)}")
    for t in txns:
        print(f"  Txn {t[0]}: {t[1]} ${t[2]:,.0f} {t[3]} (Year {t[4]}, Disclosure {t[5]})")

    # Get her anomalies
    cursor.execute("""
        SELECT a.id, a.title, a.disclosure_id, d.filing_year
        FROM anomalies a
        LEFT JOIN disclosures d ON a.disclosure_id = d.id
        WHERE a.member_id = ?
        ORDER BY d.filing_year
    """, (pelosi_id,))

    anomalies = cursor.fetchall()
    print(f"\nPelosi anomalies: {len(anomalies)}")
    for a in anomalies:
        print(f"  Anomaly {a[0]}: Year {a[3]} - {a[1]}")

conn.close()

