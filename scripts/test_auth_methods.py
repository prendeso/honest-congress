import requests
import json

api_key = '154cc513278c4b9da1f00ad8aa89d7ca84790a95'

print('Testing QuiverQuant API authentication methods...\n')

# Try different authentication methods
auth_methods = [
    ('Query param apikey', lambda url: (url, {'params': {'apikey': api_key}})),
    ('Header Authorization Bearer', lambda url: (url, {'headers': {'Authorization': f'Bearer {api_key}'}})),
    ('Header Authorization ApiKey', lambda url: (url, {'headers': {'Authorization': f'ApiKey {api_key}'}})),
    ('Header X-API-Key', lambda url: (url, {'headers': {'X-API-Key': api_key}})),
]

url = 'https://api.quiverquant.com/beta/live/housetrading'

for method_name, build_request in auth_methods:
    try:
        req_url, kwargs = build_request(url)
        r = requests.get(req_url, timeout=10, **kwargs)
        print(f'{method_name}: Status {r.status_code}')

        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                print(f'  ✅ SUCCESS! Got {len(data)} records')
                if data:
                    print(f'  Sample: {json.dumps(data[0], indent=4)[:400]}')
            else:
                print(f'  Response: {str(data)[:200]}')
        else:
            print(f'  Response: {r.text[:150]}')
    except Exception as e:
        print(f'{method_name}: {type(e).__name__} - {str(e)[:100]}')
    print()

