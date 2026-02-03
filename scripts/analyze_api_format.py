import requests
import json

api_key = '154cc513278c4b9da1f00ad8aa89d7ca84790a95'

# Test with Bearer token
headers = {'Authorization': f'Bearer {api_key}'}

print('=== QuiverQuant API Analysis ===\n')

# Test House Trading endpoint
print('1. House Trading Endpoint')
r = requests.get('https://api.quiverquant.com/beta/live/housetrading', headers=headers, timeout=10)
print(f'   Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'   Total records: {len(data)}')
    print(f'   Sample record:')
    print(json.dumps(data[0], indent=2))
    print(f'\n   Data fields: {list(data[0].keys())}')

    # Analyze transactions
    transactions = {}
    for record in data[:100]:
        txn = record.get('Transaction', 'Unknown')
        transactions[txn] = transactions.get(txn, 0) + 1
    print(f'\n   Transaction types in first 100: {transactions}')

print('\n2. Senate Trading Endpoint')
r = requests.get('https://api.quiverquant.com/beta/live/senatetrading', headers=headers, timeout=10)
print(f'   Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'   Total records: {len(data)}')
    if data:
        print(f'   Sample record:')
        print(json.dumps(data[0], indent=2)[:400])

print('\n3. Data Structure Summary')
print('   Fields available:')
print('   - Representative: Member name')
print('   - BioGuideID: Member ID')
print('   - Date: Transaction date')
print('   - Ticker: Stock symbol')
print('   - Transaction: Purchase/Sale/Exchange')
print('   - Range: Amount range as string (e.g., "$1,001 - $15,000")')
print('   - Amount: Numeric amount (midpoint)')
print('   - last_modified: When record was updated')

