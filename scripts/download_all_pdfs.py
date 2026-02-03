"""
Download all available PDFs for disclosures in the database.
This creates a paper trail by storing local copies of all official documents.

Usage:
    python scripts/download_all_pdfs.py                    # Download all
    python scripts/download_all_pdfs.py --doc-id 20033725  # Download specific
    python scripts/download_all_pdfs.py --limit 10         # Download first 10
    python scripts/download_all_pdfs.py --force            # Re-download all
"""
import os
import sys
import logging
import requests
import time
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import SessionLocal, init_db
from src.db.models import Disclosure
from src.ingestion.orchestrator import IngestionOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# PDF storage directory
PDF_DIR = Path(__file__).parent.parent / "data" / "disclosures"

# House Clerk URL patterns
HOUSE_PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
HOUSE_FD_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{doc_id}.pdf"

def download_pdf(url: str, save_path: Path, timeout: int = 30) -> bool:
    """Download a PDF from URL and save locally."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(url, headers=headers, timeout=timeout)

        if response.status_code == 200:
            # Check if it's actually a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' in content_type.lower() or response.content[:4] == b'%PDF':
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"  ✓ Downloaded: {save_path.name} ({len(response.content)} bytes)")
                return True
            else:
                logger.warning(f"  ✗ Not a PDF: {url}")
                return False
        elif response.status_code == 404:
            logger.debug(f"  ✗ Not found (404): {url}")
            return False
        else:
            logger.warning(f"  ✗ HTTP {response.status_code}: {url}")
            return False

    except requests.exceptions.Timeout:
        logger.warning(f"  ✗ Timeout: {url}")
        return False
    except Exception as e:
        logger.error(f"  ✗ Error downloading {url}: {e}")
        return False


def fix_future_dates(db):
    """Fix any disclosure records with future filing dates."""
    now = datetime.utcnow()

    # Find disclosures with future dates
    future_disclosures = db.query(Disclosure).filter(
        Disclosure.filing_date > now
    ).all()

    if future_disclosures:
        logger.info(f"\nFixing {len(future_disclosures)} disclosures with future dates...")
        for d in future_disclosures:
            old_date = d.filing_date
            d.filing_date = now
            logger.info(f"  Fixed {d.document_id}: {old_date} -> {now}")
        db.commit()
    else:
        logger.info("No future-dated disclosures found.")


def build_pdf_urls(disclosure: Disclosure) -> list:
    """Build possible PDF URLs for a disclosure."""
    urls = []
    doc_id = disclosure.document_id
    year = disclosure.filing_year

    # If there's an existing URL, try it first
    if disclosure.document_url:
        urls.append(disclosure.document_url)

    # Skip non-House disclosures or API-sourced records
    if doc_id.startswith("QANT_"):
        return urls  # QuiverQuant API data, no PDF

    # Try PTR URL pattern
    if disclosure.is_ptr:
        urls.append(HOUSE_PTR_URL.format(year=year, doc_id=doc_id))
        # Also try current year if different
        current_year = datetime.now().year
        if year != current_year:
            urls.append(HOUSE_PTR_URL.format(year=current_year, doc_id=doc_id))
    else:
        urls.append(HOUSE_FD_URL.format(year=year, doc_id=doc_id))
        current_year = datetime.now().year
        if year != current_year:
            urls.append(HOUSE_FD_URL.format(year=current_year, doc_id=doc_id))

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def download_all_pdfs(limit: int = None, force: bool = False):
    """Download PDFs for all disclosures in database."""
    init_db()
    db = SessionLocal()

    try:
        # First fix any future dates
        fix_future_dates(db)

        # Get all disclosures
        query = db.query(Disclosure)
        if limit:
            query = query.limit(limit)

        disclosures = query.all()
        logger.info(f"\nProcessing {len(disclosures)} disclosures...")

        stats = {
            "downloaded": 0,
            "already_exists": 0,
            "not_found": 0,
            "api_source": 0,
            "errors": 0
        }

        for disclosure in disclosures:
            doc_id = disclosure.document_id

            # Skip API-sourced records (no PDF available)
            if doc_id.startswith("QANT_"):
                stats["api_source"] += 1
                continue

            # Determine save path
            subdir = "ptr" if disclosure.is_ptr else "fd"
            save_path = PDF_DIR / subdir / str(disclosure.filing_year) / f"{doc_id}.pdf"

            # Skip if already downloaded
            if save_path.exists() and not force:
                stats["already_exists"] += 1
                continue

            # Try to download
            urls = build_pdf_urls(disclosure)
            if not urls:
                stats["not_found"] += 1
                continue

            logger.info(f"\n{doc_id} (Year: {disclosure.filing_year}, PTR: {disclosure.is_ptr})")

            downloaded = False
            for url in urls:
                if download_pdf(url, save_path):
                    # Update the disclosure URL if we found a working one
                    if url != disclosure.document_url:
                        disclosure.document_url = url
                    downloaded = True
                    break
                time.sleep(0.3)  # Be nice to servers

            if downloaded:
                stats["downloaded"] += 1
            else:
                stats["not_found"] += 1

            time.sleep(0.5)  # Rate limiting

        db.commit()

        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info("DOWNLOAD SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Downloaded:      {stats['downloaded']}")
        logger.info(f"Already exists:  {stats['already_exists']}")
        logger.info(f"Not found:       {stats['not_found']}")
        logger.info(f"API source:      {stats['api_source']} (no PDF)")
        logger.info(f"Errors:          {stats['errors']}")
        logger.info(f"{'='*60}")

        # Show where files are stored
        logger.info(f"\nPDFs stored in: {PDF_DIR.absolute()}")

    finally:
        db.close()


def download_specific(doc_id: str):
    """Download PDF for a specific document ID."""
    init_db()
    db = SessionLocal()

    try:
        disclosure = db.query(Disclosure).filter(
            Disclosure.document_id == doc_id
        ).first()

        if not disclosure:
            logger.error(f"Disclosure {doc_id} not found in database")
            return False

        logger.info(f"Found disclosure: {doc_id}")
        logger.info(f"  Year: {disclosure.filing_year}")
        logger.info(f"  PTR: {disclosure.is_ptr}")
        logger.info(f"  Filing date: {disclosure.filing_date}")

        # Fix future date if needed
        now = datetime.utcnow()
        if disclosure.filing_date and disclosure.filing_date > now:
            logger.info(f"  Fixing future date: {disclosure.filing_date} -> {now}")
            disclosure.filing_date = now
            db.commit()

        # Determine save path
        subdir = "ptr" if disclosure.is_ptr else "fd"
        save_path = PDF_DIR / subdir / str(disclosure.filing_year) / f"{doc_id}.pdf"

        # Build URLs to try
        urls = build_pdf_urls(disclosure)

        # Also try current year explicitly for the specific case mentioned
        current_year = datetime.now().year
        if disclosure.is_ptr:
            urls.append(f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{current_year}/{doc_id}.pdf")

        logger.info(f"  Trying {len(urls)} URL(s)...")

        for url in urls:
            logger.info(f"  URL: {url}")
            if download_pdf(url, save_path):
                disclosure.document_url = url
                db.commit()
                logger.info(f"\n✓ Success! PDF saved to: {save_path}")
                return True
            time.sleep(0.5)

        logger.error(f"\n✗ Could not download PDF for {doc_id}")
        return False

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download PDFs for congressional disclosures")
    parser.add_argument("--doc-id", help="Download specific document by ID")
    parser.add_argument("--limit", type=int, help="Limit number of disclosures to process")
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    parser.add_argument("--use-orchestrator", action="store_true", help="Use IngestionOrchestrator (recommended)")

    args = parser.parse_args()

    if args.use_orchestrator or (not args.doc_id):
        # Use the orchestrator for bulk downloads
        init_db()
        db = SessionLocal()
        try:
            orchestrator = IngestionOrchestrator()

            if args.doc_id:
                # Single document
                disclosure = db.query(Disclosure).filter(
                    Disclosure.document_id == args.doc_id
                ).first()
                if disclosure:
                    result = orchestrator.download_disclosure_pdf(disclosure, force=args.force)
                    if result:
                        print(f"✓ Downloaded: {result}")
                    else:
                        print(f"✗ Failed to download {args.doc_id}")
                else:
                    print(f"✗ Disclosure {args.doc_id} not found")
            else:
                # All documents
                results = orchestrator.download_all_pdfs(
                    db,
                    limit=args.limit,
                    force=args.force
                )
                print(f"\nResults: {results}")
        finally:
            db.close()
    elif args.doc_id:
        download_specific(args.doc_id)
    else:
        download_all_pdfs(limit=args.limit, force=args.force)

