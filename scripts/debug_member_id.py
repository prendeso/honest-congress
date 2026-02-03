"""Debug member ID mismatch."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), '..', 'honest_congress.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Find Pelosi in members table
cursor.execute("SELECT id, first_name, last_name FROM members WHERE last_name = 'Pelosi'")
pelosi_members = cursor.fetchall()
print("Pelosi in members table:")
for m in pelosi_members:
    print(f"  ID {m[0]}: {m[1]} {m[2]}")

# Find anomalies with Pelosi-like titles or member_id
print("\nAnomalies with Pelosi:")
cursor.execute("""
    SELECT a.id, a.member_id, a.title, m.first_name, m.last_name 
    FROM anomalies a 
    JOIN members m ON a.member_id = m.id 
    WHERE m.last_name = 'Pelosi'
""")
anomalies = cursor.fetchall()
print(f"  Found: {len(anomalies)}")
for a in anomalies:
    print(f"  Anomaly {a[0]}: member_id={a[1]} ({a[3]} {a[4]}) - {a[2]}")

# Check what member_id Pelosi's anomalies have
print("\nAll member_ids in anomalies table:")
cursor.execute("SELECT DISTINCT member_id FROM anomalies ORDER BY member_id")
ids = cursor.fetchall()
print(f"  {[i[0] for i in ids]}")

# Check member 99 (from earlier output)
cursor.execute("SELECT id, first_name, last_name FROM members WHERE id = 99")
m99 = cursor.fetchone()
if m99:
    print(f"\nMember ID 99: {m99[1]} {m99[2]}")

conn.close()

