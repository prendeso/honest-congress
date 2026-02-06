"""
Migration script to add materialized count columns to members table.

This script:
1. Adds anomaly_count and disclosure_count columns to members table
2. Populates them with current counts from related tables
3. Creates indexes for fast sorting
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from src.db import get_db_session, Member, Disclosure, Anomaly
from sqlalchemy import func

def migrate():
    """Add materialized count columns and populate them."""
    print("Starting migration: Adding materialized count columns to members table")

    db = next(get_db_session())

    try:
        # Step 1: Add columns if they don't exist
        print("\n[1/4] Adding anomaly_count and disclosure_count columns...")

        # Check if columns exist
        result = db.execute(text("PRAGMA table_info(members)"))
        columns = [row[1] for row in result]

        if 'anomaly_count' not in columns:
            db.execute(text("ALTER TABLE members ADD COLUMN anomaly_count INTEGER DEFAULT 0"))
            print("  ✓ Added anomaly_count column")
        else:
            print("  → anomaly_count column already exists")

        if 'disclosure_count' not in columns:
            db.execute(text("ALTER TABLE members ADD COLUMN disclosure_count INTEGER DEFAULT 0"))
            print("  ✓ Added disclosure_count column")
        else:
            print("  → disclosure_count column already exists")

        db.commit()

        # Step 2: Populate anomaly_count
        print("\n[2/4] Calculating anomaly counts...")
        anomaly_counts = db.query(
            Anomaly.member_id,
            func.count(Anomaly.id).label('count')
        ).group_by(Anomaly.member_id).all()

        print(f"  Found {len(anomaly_counts)} members with anomalies")

        for member_id, count in anomaly_counts:
            db.execute(
                text("UPDATE members SET anomaly_count = :count WHERE id = :id"),
                {"count": count, "id": member_id}
            )

        db.commit()
        print("  ✓ Updated anomaly counts")

        # Step 3: Populate disclosure_count
        print("\n[3/4] Calculating disclosure counts...")
        disclosure_counts = db.query(
            Disclosure.member_id,
            func.count(Disclosure.id).label('count')
        ).group_by(Disclosure.member_id).all()

        print(f"  Found {len(disclosure_counts)} members with disclosures")

        for member_id, count in disclosure_counts:
            db.execute(
                text("UPDATE members SET disclosure_count = :count WHERE id = :id"),
                {"count": count, "id": member_id}
            )

        db.commit()
        print("  ✓ Updated disclosure counts")

        # Step 4: Create indexes
        print("\n[4/4] Creating indexes...")

        try:
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_members_anomaly_count ON members(anomaly_count)"))
            print("  ✓ Created index on anomaly_count")
        except Exception as e:
            print(f"  → Index on anomaly_count may already exist: {e}")

        try:
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_members_disclosure_count ON members(disclosure_count)"))
            print("  ✓ Created index on disclosure_count")
        except Exception as e:
            print(f"  → Index on disclosure_count may already exist: {e}")

        db.commit()

        # Verify results
        print("\n[Verification] Checking results...")
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total_members,
                SUM(CASE WHEN anomaly_count > 0 THEN 1 ELSE 0 END) as members_with_anomalies,
                SUM(CASE WHEN disclosure_count > 0 THEN 1 ELSE 0 END) as members_with_disclosures,
                MAX(anomaly_count) as max_anomalies,
                MAX(disclosure_count) as max_disclosures
            FROM members
        """)).first()

        print(f"  Total members: {result[0]}")
        print(f"  Members with anomalies: {result[1]}")
        print(f"  Members with disclosures: {result[2]}")
        print(f"  Max anomaly count: {result[3]}")
        print(f"  Max disclosure count: {result[4]}")

        print("\n✅ Migration completed successfully!")
        print("\nNext steps:")
        print("1. Update members.py to use materialized columns instead of subqueries")
        print("2. Add triggers or update logic to maintain counts when data changes")
        print("3. Restart the server to apply changes")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()

