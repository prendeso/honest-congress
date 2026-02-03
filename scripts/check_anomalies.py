from src.db.database import SessionLocal, init_db
from src.db.models import Anomaly, Member

init_db()
db = SessionLocal()

# Find Pelosi's member ID
pelosi = db.query(Member).filter(Member.last_name == 'Pelosi').first()
print(f'Pelosi ID: {pelosi.id}')

# Get all her anomalies
anomalies = db.query(Anomaly).filter(Anomaly.member_id == pelosi.id).all()
print(f'Total anomalies in DB: {len(anomalies)}')
for a in anomalies:
    print(f'  ID {a.id}: {a.title} (disclosure_id={a.disclosure_id})')

db.close()

