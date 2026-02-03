"""Check QuiverQuant BioGuideID matching against our database."""
import requests
from src.db.database import SessionLocal
from src.db.models import Member

api_key = '154cc513278c4b9da1f00ad8aa89d7ca84790a95'
headers = {'Authorization': f'Bearer {api_key}'}

print('=== Checking Senate BioGuideID Matching ===\n')

# Get Senate trades from QuiverQuant
r = requests.get('https://api.quiverquant.com/beta/live/senatetrading', headers=headers, timeout=30)
senate_trades = r.json()

print(f'Total Senate trades from QuiverQuant: {len(senate_trades)}')

# Get unique BioGuideIDs from QuiverQuant
qant_bioguides = set()
for trade in senate_trades:
    bio = trade.get('BioGuideID')
    if bio:
        qant_bioguides.add(bio)

print(f'Unique Senate BioGuideIDs in QuiverQuant: {len(qant_bioguides)}')

# Check against our database
db = SessionLocal()
our_bioguides = set([m.bioguide_id for m in db.query(Member).all() if m.bioguide_id])

print(f'Total BioGuideIDs in our database: {len(our_bioguides)}')

# Find mismatches
missing_in_db = qant_bioguides - our_bioguides
print(f'\nBioGuideIDs in QuiverQuant but NOT in our DB: {len(missing_in_db)}')

if missing_in_db:
    print('\nSample missing BioGuideIDs:')
    for bio in list(missing_in_db)[:10]:
        # Find name for this BioGuideID
        for trade in senate_trades:
            if trade.get('BioGuideID') == bio:
                print(f'  {bio}: {trade.get("Senator", "Unknown")}')
                break

# Check how many would match with current logic
matches = len(qant_bioguides & our_bioguides)
print(f'\nBioGuideIDs that WOULD match: {matches}/{len(qant_bioguides)} ({matches*100//len(qant_bioguides)}%)')

db.close()

