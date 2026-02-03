"""Check Pelosi anomalies specifically."""
import requests

# Find Pelosi member ID first
response = requests.get('http://127.0.0.1:8000/api/members?last_name=Pelosi')
data = response.json()
print(f"Members search result: {data}")

if data.get('members'):
    pelosi = data['members'][0]
    pelosi_id = pelosi['id']
    print(f"\nPelosi ID: {pelosi_id}")

    # Get anomalies for Pelosi
    response = requests.get(f'http://127.0.0.1:8000/api/anomalies?member_id={pelosi_id}')
    data = response.json()
    print(f"Anomalies for Pelosi: {data.get('total', 0)}")

    for a in data.get('anomalies', []):
        print(f"  ID {a['id']}: {a['title']}")

