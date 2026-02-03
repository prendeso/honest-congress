#!/usr/bin/env python
"""
Fix anomalies where computed_value == threshold_value.
These should not have been flagged - anomalies require EXCEEDING the threshold, not just matching it.
"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.db import get_db, Anomaly

def main():
    print("=" * 60)
    print("Fixing anomalies where computed_value == threshold_value")
    print("=" * 60)

    with get_db() as db:
        # Get all anomalies with both values set
        all_anomalies = db.query(Anomaly).filter(
            Anomaly.computed_value.isnot(None),
            Anomaly.threshold_value.isnot(None)
        ).all()

        print(f"\nTotal anomalies with computed/threshold values: {len(all_anomalies)}")

        # Find bad anomalies where computed == threshold (using float comparison)
        bad_anomalies = []
        for a in all_anomalies:
            computed = float(a.computed_value) if a.computed_value else None
            threshold = float(a.threshold_value) if a.threshold_value else None
            if computed is not None and threshold is not None and computed == threshold:
                bad_anomalies.append(a)

        print(f"Found {len(bad_anomalies)} anomalies where computed_value == threshold_value")

        if not bad_anomalies:
            print("No invalid anomalies found. Database is clean!")
            return

        print("\nAnomalies to delete:")
        for a in bad_anomalies:
            print(f"  - {a.title}")
            print(f"    Type: {a.anomaly_type}, Computed: {a.computed_value}, Threshold: {a.threshold_value}")

        # Delete them
        for a in bad_anomalies:
            db.delete(a)

        db.commit()
        print(f"\n✓ Deleted {len(bad_anomalies)} invalid anomalies")

        # Show remaining count
        remaining = db.query(Anomaly).count()
        print(f"✓ Remaining anomalies: {remaining}")

if __name__ == "__main__":
    main()

