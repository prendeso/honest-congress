"""Test XML parsing from House Clerk."""
import requests
import xml.etree.ElementTree as ET

url = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024FD.xml'
r = requests.get(url, timeout=10)

print('Status:', r.status_code)
print('Content length:', len(r.content))
print('Content type:', r.headers.get('content-type'))
print()

print('Trying ET.fromstring with content...')
try:
    root = ET.fromstring(r.content)
    print('✓ SUCCESS!')
    members = root.findall('Member')
    print(f'  Found {len(members)} members')
    if members:
        first = members[0]
        print(f'  First member: {first.findtext("Last", "N/A")}, {first.findtext("First", "N/A")}')
except Exception as e:
    print(f'✗ FAILED: {str(e)[:100]}')

