from src.db.database import SessionLocal, init_db
from src.db.models import Anomaly, Member, Disclosure, Transaction

init_db()
db = SessionLocal()

output = []

# Find NVDA related anomalies
nvda_anomalies = db.query(Anomaly).filter(Anomaly.title.ilike('%NVDA%')).all()
output.append(f'NVDA anomalies: {len(nvda_anomalies)}')

for a in nvda_anomalies:
    member = db.query(Member).filter(Member.id == a.member_id).first()
    disclosure = db.query(Disclosure).filter(Disclosure.id == a.disclosure_id).first() if a.disclosure_id else None
    year = disclosure.filing_year if disclosure else 'N/A'
    output.append(f'\nAnomaly ID {a.id}:')
    output.append(f'  Member: {member.first_name} {member.last_name}')
    output.append(f'  Year: {year}')
    output.append(f'  Title: {a.title}')
    output.append(f'  Disclosure ID: {a.disclosure_id}')
    output.append(f'  Transaction ID: {a.transaction_id}')

db.close()

with open('nvda_check.txt', 'w') as f:
    f.write('\n'.join(output))
print('Output written to nvda_check.txt')

