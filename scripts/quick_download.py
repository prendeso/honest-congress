"""Quick script to download a specific PDF and fix future dates."""
import requests
from pathlib import Path
from datetime import datetime

# The PDF URL you found
DOC_ID = "20033725"
YEAR = 2026
PDF_URL = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DOC_ID}.pdf"

# Where to save
SAVE_DIR = Path(__file__).parent.parent / "data" / "disclosures" / "ptr" / str(YEAR)
SAVE_PATH = SAVE_DIR / f"{DOC_ID}.pdf"

def download():
    print(f"Downloading: {PDF_URL}")
    print(f"Save to: {SAVE_PATH}")

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    response = requests.get(PDF_URL, headers=headers, timeout=30)

    if response.status_code == 200:
        with open(SAVE_PATH, 'wb') as f:
            f.write(response.content)
        print(f"✓ Downloaded! Size: {len(response.content)} bytes")
        print(f"✓ Saved to: {SAVE_PATH.absolute()}")
        return True
    else:
        print(f"✗ Failed: HTTP {response.status_code}")
        return False

def fix_database():
    """Fix the future date in the database."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))

        from src.db.database import SessionLocal, init_db
        from src.db.models import Disclosure

        init_db()
        db = SessionLocal()

        # Find the disclosure
        disclosure = db.query(Disclosure).filter(
            Disclosure.document_id == DOC_ID
        ).first()

        if disclosure:
            print(f"\nDatabase record found:")
            print(f"  ID: {disclosure.id}")
            print(f"  Document ID: {disclosure.document_id}")
            print(f"  Filing Year: {disclosure.filing_year}")
            print(f"  Filing Date: {disclosure.filing_date}")
            print(f"  URL: {disclosure.document_url}")

            # Fix future date
            now = datetime.utcnow()
            if disclosure.filing_date and disclosure.filing_date > now:
                old_date = disclosure.filing_date
                disclosure.filing_date = now
                disclosure.document_url = PDF_URL
                db.commit()
                print(f"\n✓ Fixed future date: {old_date} -> {now}")
                print(f"✓ Updated URL to: {PDF_URL}")
            else:
                # Just update URL if needed
                if disclosure.document_url != PDF_URL:
                    disclosure.document_url = PDF_URL
                    db.commit()
                    print(f"\n✓ Updated URL to: {PDF_URL}")
        else:
            print(f"\n✗ Disclosure {DOC_ID} not found in database")

        db.close()

    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("Downloading PTR PDF and fixing database")
    print("=" * 60)

    # Download PDF
    if download():
        # Fix database
        fix_database()

    print("\nDone!")

