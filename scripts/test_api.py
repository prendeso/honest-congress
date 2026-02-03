"""Test API response for anomalies."""
import requests
import json

try:
    response = requests.get('http://127.0.0.1:8000/api/anomalies?page_size=100')
    data = response.json()

    print(f"Total anomalies in DB: {data.get('total', 0)}")
    print(f"Anomalies returned: {len(data.get('anomalies', []))}")

    print("\nPelosi anomalies from API:")
    for a in data.get('anomalies', []):
        if 'Pelosi' in a.get('member_name', ''):
            print(f"  ID {a['id']}: {a['title']}")

    print("\nAll large trade anomalies:")
    for a in data.get('anomalies', []):
        if a.get('anomaly_type') == 'large_trade':
            print(f"  ID {a['id']}: {a['member_name']} - {a['title']}")

except Exception as e:
    print(f"Error: {e}")

