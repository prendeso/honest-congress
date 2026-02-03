"""Add historical members to database for better QuiverQuant matching."""
import requests
from src.db.database import SessionLocal
from src.db.models import Member, Chamber

# Missing BioGuideIDs from QuiverQuant
missing_bioguides = [
    'B000575',  # Roy Blunt
    'R000307',  # Pat Roberts
    'L000594',  # Kelly Loeffler
    'M001170',  # Claire McCaskill
    'H001041',  # Dean Heller
    'I000024',  # James M. Inhofe
    'T000461',  # Pat Toomey
    'H001069',  # Heidi Heitkamp
    'P000612',  # David Perdue
    'C000174',  # Thomas R. Carper
]

print('=== Adding Historical Members for QuiverQuant Matching ===\n')

# Fetch historical legislators
HISTORICAL_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages/legislators-historical.json"
print('Fetching historical legislators...')
r = requests.get(HISTORICAL_URL, timeout=30)
historical = r.json()

print(f'Loaded {len(historical)} historical legislators')

# Find missing members
db = SessionLocal()
added = 0
already_exists = 0

for bioguide in missing_bioguides:
    # Check if already in database
    existing = db.query(Member).filter(Member.bioguide_id == bioguide).first()
    if existing:
        print(f'  ✓ {bioguide}: Already in database')
        already_exists += 1
        continue

    # Find in historical data
    for leg in historical:
        leg_id = leg.get('id', {})
        if leg_id.get('bioguide') == bioguide:
            # Extract details
            name = leg.get('name', {})
            bio = leg.get('bio', {})

            # Determine chamber (use most recent term)
            terms = leg.get('terms', [])
            if not terms:
                continue

            latest_term = terms[-1]
            chamber_str = latest_term.get('type', '').lower()
            if chamber_str == 'sen':
                chamber = Chamber.SENATE
            elif chamber_str == 'rep':
                chamber = Chamber.HOUSE
            else:
                continue

            # Normalize party to uppercase for enum
            party = latest_term.get('party', '').upper()

            # Create member
            member = Member(
                bioguide_id=bioguide,
                first_name=name.get('first', ''),
                last_name=name.get('last', ''),
                party=party,
                state=latest_term.get('state', ''),
                district=latest_term.get('district'),
                chamber=chamber,
                in_office=False,  # Historical member
            )

            db.add(member)
            print(f'  ✓ {bioguide}: Added {name.get("first")} {name.get("last")} ({chamber.value}-{latest_term.get("state")})')
            added += 1
            break

db.commit()
db.close()

print(f'\n=== Summary ===')
print(f'Added: {added}')
print(f'Already existed: {already_exists}')
print(f'Total: {len(missing_bioguides)}')

if added > 0:
    print(f'\n✅ Re-run QuiverQuant import to improve Senate matching!')
    print(f'   Command: python -m src.cli ingest-trades --chamber senate')

