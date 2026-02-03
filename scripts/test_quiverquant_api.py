import requests
import json

api_key = '154cc513278c4b9da1f00ad8aa89d7ca84790a95'

print('Testing QuiverQuant API endpoints for Tier 1 access...\n')

endpoints = [
    ('House Trading', 'https://api.quiverquant.com/beta/live/housetrading'),
    ('Senate Trading', 'https://api.quiverquant.com/beta/live/senatetrading'),
    ('Congress Trades', 'https://api.quiverquant.com/beta/aggregated/congress/trades'),
    ('Historical Trades', 'https://api.quiverquant.com/beta/historical/aggregated/congress/trades'),
]

for name, url in endpoints:
    try:
        r = requests.get(url, params={'apikey': api_key}, timeout=10)
        print(f'{name}: Status {r.status_code}')
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                print(f'  Records: {len(data)}')
                if data:
                    print(f'  Sample record:')
                    print(json.dumps(data[0], indent=4)[:600])
            else:
                print(f'  Response keys: {list(data.keys()) if isinstance(data, dict) else "not dict"}')
        else:
            print(f'  Error: {r.status_code} - {r.text[:200]}')
    except Exception as e:
        print(f'{name}: Exception - {type(e).__name__}: {str(e)[:100]}')
    print()

