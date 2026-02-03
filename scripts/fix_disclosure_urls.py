"""
Fix incorrect disclosure URLs in the database.

Problem: PTR disclosures have URLs like:
  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/20026246.pdf

Should be:
  https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2024/20026246.pdf

This script fixes all incorrect URLs in the database.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from src.db.database import SessionLocal, init_db
from src.db.models import Disclosure

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://disclosures-clerk.house.gov/public_disc"


def get_correct_url(disclosure: Disclosure) -> str:
    """Generate the correct URL for a disclosure."""
    doc_id = disclosure.document_id
    year = disclosure.filing_year

    # Skip API-sourced records
    if doc_id.startswith("QANT_"):
        return disclosure.document_url or ""

    if disclosure.is_ptr:
        return f"{BASE_URL}/ptr-pdfs/{year}/{doc_id}.pdf"
    else:
        return f"{BASE_URL}/financial-pdfs/{year}/{doc_id}.pdf"


def fix_urls(dry_run: bool = True):
    """Fix all incorrect disclosure URLs in the database."""
    init_db()
    db = SessionLocal()

    try:
        # Get all non-API disclosures
        disclosures = db.query(Disclosure).filter(
            ~Disclosure.document_id.like("QANT_%")
        ).all()

        logger.info(f"Checking {len(disclosures)} disclosures...")

        fixed_count = 0
        issues = []

        for d in disclosures:
            correct_url = get_correct_url(d)
            current_url = d.document_url or ""

            # Check if URL needs fixing
            needs_fix = False
            reason = ""

            if not current_url:
                needs_fix = True
                reason = "Missing URL"
            elif current_url != correct_url:
                # Check specific issues
                if d.is_ptr and "/financial-pdfs/" in current_url:
                    needs_fix = True
                    reason = "PTR using financial-pdfs instead of ptr-pdfs"
                elif not d.is_ptr and "/ptr-pdfs/" in current_url:
                    needs_fix = True
                    reason = "FD using ptr-pdfs instead of financial-pdfs"
                elif f"/{d.filing_year}/" not in current_url:
                    needs_fix = True
                    reason = "Missing year in URL path"

            if needs_fix:
                issues.append({
                    "id": d.id,
                    "doc_id": d.document_id,
                    "is_ptr": d.is_ptr,
                    "year": d.filing_year,
                    "old_url": current_url,
                    "new_url": correct_url,
                    "reason": reason,
                })

                if not dry_run:
                    d.document_url = correct_url
                    fixed_count += 1

        if not dry_run:
            db.commit()

        # Report
        logger.info(f"\n{'='*70}")
        logger.info(f"URL FIX {'PREVIEW' if dry_run else 'RESULTS'}")
        logger.info(f"{'='*70}")
        logger.info(f"Total disclosures checked: {len(disclosures)}")
        logger.info(f"Issues found: {len(issues)}")

        if issues:
            # Group by reason
            by_reason = {}
            for issue in issues:
                reason = issue["reason"]
                by_reason[reason] = by_reason.get(reason, 0) + 1

            logger.info(f"\nIssues by type:")
            for reason, count in by_reason.items():
                logger.info(f"  {reason}: {count}")

            logger.info(f"\nSample fixes (first 10):")
            for issue in issues[:10]:
                logger.info(f"\n  Doc ID: {issue['doc_id']} (PTR: {issue['is_ptr']}, Year: {issue['year']})")
                logger.info(f"    Old: {issue['old_url']}")
                logger.info(f"    New: {issue['new_url']}")
                logger.info(f"    Reason: {issue['reason']}")

        if dry_run:
            logger.info(f"\n*** DRY RUN - No changes made ***")
            logger.info(f"Run with --apply to fix these URLs")
        else:
            logger.info(f"\n✓ Fixed {fixed_count} URLs")

        logger.info(f"{'='*70}\n")

        return issues

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fix incorrect disclosure URLs")
    parser.add_argument("--apply", action="store_true", help="Actually apply fixes (default is dry-run)")

    args = parser.parse_args()

    if args.apply:
        logger.info("Applying URL fixes...")
        fix_urls(dry_run=False)
    else:
        logger.info("Running in dry-run mode (use --apply to make changes)...")
        fix_urls(dry_run=True)

