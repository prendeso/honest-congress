"""Unified ingestion orchestrator."""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db import Chamber, Disclosure, Member, Party, get_db
from src.db.models import Asset, AssetType, Liability, Transaction, TransactionType
from src.ingestion import _helpers
from src.ingestion.congress_gov import CongressGovClient
from src.ingestion.date_utils import choose_filing_date, choose_transaction_date
from src.ingestion.house import HouseIngester
from src.ingestion.senate import SenateIngester, SenatePTRIngester
from src.parsing.confidence import score_fd_parse, score_ptr_parse
from src.parsing.pdf_parser import DisclosureParser
from src.parsing.ptr_parser import PTRParser

logger = logging.getLogger(__name__)

# Default data directory
DATA_DIR = Path("data")
DISCLOSURES_DIR = DATA_DIR / "disclosures"


class IngestionOrchestrator:
    """
    Orchestrates data ingestion from multiple sources.

    Combines unitedstates/congress-legislators/Congress.gov (member metadata), House Clerk (House disclosures),
    and Senate eFD (Senate disclosures) into a unified ingestion pipeline.
    """

    def __init__(self, data_dir: Path | None = None):
        self.house = HouseIngester()
        self.senate = SenateIngester()
        self.senate_ptr = SenatePTRIngester()
        self.member_client = CongressGovClient()

        # PDF Parsers
        self.disclosure_parser = DisclosureParser()
        self.ptr_parser = PTRParser()

        self.data_dir = data_dir or DATA_DIR
        self.disclosures_dir = self.data_dir / "disclosures"
        self.disclosures_dir.mkdir(parents=True, exist_ok=True)

    def sync_members(self, db: Session) -> int:
        """
        Sync member data from unitedstates/congress-legislators/Congress.gov to database.

        Args:
            db: Database session

        Returns:
            Number of members synced
        """
        logger.info("Syncing members from unitedstates/congress-legislators/Congress.gov...")

        members = self.member_client.get_current_members()
        synced = 0

        for m in members:
            try:
                # Check if member exists
                existing = db.query(Member).filter(Member.bioguide_id == m["bioguide_id"]).first()

                if existing:
                    # Update existing member
                    existing.first_name = m["first_name"]
                    existing.last_name = m["last_name"]
                    existing.party = Party(m["party"])
                    existing.state = m["state"]
                    existing.district = m.get("district")
                    existing.in_office = m.get("in_office", True)
                else:
                    # Create new member
                    new_member = Member(
                        bioguide_id=m["bioguide_id"],
                        first_name=m["first_name"],
                        last_name=m["last_name"],
                        chamber=Chamber(m["chamber"]),
                        party=Party(m["party"]),
                        state=m["state"],
                        district=m.get("district"),
                        in_office=m.get("in_office", True),
                    )
                    db.add(new_member)

                synced += 1

            except Exception as e:
                logger.error(f"Error syncing member {m.get('bioguide_id')}: {e}")

        db.commit()
        logger.info(f"Synced {synced} members")
        return synced

    def sync_all_members(self, db: Session) -> Dict[str, int]:
        """
        Sync ALL member data (current + historical) from unitedstates.io to database.

        Args:
            db: Database session

        Returns:
            Dict with synced counts: {"total": x, "current": y, "historical": z, "updated": w}
        """
        logger.info("Syncing ALL members (current + historical)...")

        members = self.member_client.get_all_members()

        results = {"total": 0, "current": 0, "historical": 0, "updated": 0, "created": 0}

        for m in members:
            try:
                # Check if member exists
                existing = db.query(Member).filter(Member.bioguide_id == m["bioguide_id"]).first()

                is_current = m.get("in_office", False)

                if existing:
                    # Update existing member
                    existing.first_name = m["first_name"]
                    existing.last_name = m["last_name"]
                    existing.party = Party(m["party"])
                    existing.state = m["state"]
                    existing.district = m.get("district")
                    existing.in_office = is_current
                    results["updated"] += 1
                else:
                    # Create new member
                    new_member = Member(
                        bioguide_id=m["bioguide_id"],
                        first_name=m["first_name"],
                        last_name=m["last_name"],
                        chamber=Chamber(m["chamber"]),
                        party=Party(m["party"]),
                        state=m["state"],
                        district=m.get("district"),
                        in_office=is_current,
                    )
                    db.add(new_member)
                    results["created"] += 1

                results["total"] += 1
                if is_current:
                    results["current"] += 1
                else:
                    results["historical"] += 1

            except Exception as e:
                logger.error(f"Error syncing member {m.get('bioguide_id')}: {e}")

        db.commit()
        logger.info(
            f"Synced {results['total']} members (current: {results['current']}, historical: {results['historical']})"
        )
        return results

    def sync_house_disclosures(
        self, db: Session, year: int | None = None, download_pdfs: bool = False
    ) -> int:
        """
        Sync House disclosures from XML index.

        Args:
            db: Database session
            year: Filing year (defaults to current year)
            download_pdfs: Whether to download PDF files

        Returns:
            Number of disclosures synced
        """
        year = year or datetime.now().year
        logger.info(f"Syncing House disclosures for {year}...")

        disclosures = self.house.fetch_annual_xml_index(year)
        synced = 0

        for d in disclosures:
            try:
                # Find matching member
                member = (
                    db.query(Member)
                    .filter(
                        Member.last_name.ilike(d["last_name"]),
                        Member.first_name.ilike(f"{d['first_name']}%"),
                        Member.chamber == Chamber.HOUSE,
                        Member.state == d["state"],
                    )
                    .first()
                )

                if not member:
                    logger.debug(
                        f"Member not found for disclosure: "
                        f"{d['first_name']} {d['last_name']} ({d['state']})"
                    )
                    continue

                # Check if disclosure exists
                existing = (
                    db.query(Disclosure).filter(Disclosure.document_id == d["document_id"]).first()
                )

                if existing:
                    continue  # Already have this disclosure

                # Create disclosure record
                disclosure = Disclosure(
                    member_id=member.id,
                    filing_year=d["filing_year"],
                    filing_type=d["filing_type"],
                    filing_date=choose_filing_date(d.get("filing_date"), d.get("filing_year")),
                    document_id=d["document_id"],
                    document_url=d["document_url"],
                    parsed=False,
                )
                db.add(disclosure)

                # Optionally download PDF
                if download_pdfs and d["document_url"]:
                    pdf_path = (
                        self.disclosures_dir / "house" / str(year) / f"{d['document_id']}.pdf"
                    )
                    self.house.download_disclosure(d["document_url"], str(pdf_path))

                synced += 1

            except Exception as e:
                logger.error(f"Error syncing disclosure {d.get('document_id')}: {e}")

        db.commit()
        logger.info(f"Synced {synced} House disclosures for {year}")
        return synced

    def sync_house_ptrs(
        self, db: Session, year: int | None = None, download_pdfs: bool = False
    ) -> int:
        """
        Sync House Periodic Transaction Reports (PTRs) from XML index.
        PTRs contain individual stock trades filed within 45 days of transaction.

        Args:
            db: Database session
            year: Filing year (defaults to current year)
            download_pdfs: Whether to download PDF files

        Returns:
            Number of PTRs synced
        """
        year = year or datetime.now().year
        logger.info(f"Syncing House PTRs for {year}...")

        ptrs = self.house.fetch_ptr_xml_index(year)
        synced = 0

        for d in ptrs:
            try:
                # Find matching member
                member = (
                    db.query(Member)
                    .filter(
                        Member.last_name.ilike(d["last_name"]),
                        Member.first_name.ilike(f"{d['first_name']}%"),
                        Member.chamber == Chamber.HOUSE,
                        Member.state == d["state"],
                    )
                    .first()
                )

                if not member:
                    logger.debug(
                        f"Member not found for PTR: "
                        f"{d['first_name']} {d['last_name']} ({d['state']})"
                    )
                    continue

                # Check if disclosure exists
                existing = (
                    db.query(Disclosure).filter(Disclosure.document_id == d["document_id"]).first()
                )

                if existing:
                    continue  # Already have this PTR

                # Create PTR disclosure record
                disclosure = Disclosure(
                    member_id=member.id,
                    filing_year=d["filing_year"],
                    filing_type=d["filing_type"],
                    filing_date=choose_filing_date(d.get("filing_date"), d.get("filing_year")),
                    document_id=d["document_id"],
                    document_url=d["document_url"],
                    is_ptr=True,  # Mark as PTR
                    parsed=False,
                )
                db.add(disclosure)

                # Optionally download PDF
                if download_pdfs and d["document_url"]:
                    pdf_path = (
                        self.disclosures_dir
                        / "house"
                        / "ptr"
                        / str(year)
                        / f"{d['document_id']}.pdf"
                    )
                    self.house.download_disclosure(d["document_url"], str(pdf_path))

                synced += 1

            except Exception as e:
                logger.error(f"Error syncing PTR {d.get('document_id')}: {e}")

        db.commit()
        logger.info(f"Synced {synced} House PTRs for {year}")
        return synced

    def sync_senate_disclosures(
        self, db: Session, year: int | None = None, download_files: bool = False
    ) -> int:
        """
        Sync Senate disclosures.

        Args:
            db: Database session
            year: Filing year (defaults to current year)
            download_files: Whether to download disclosure files

        Returns:
            Number of disclosures synced
        """
        year = year or datetime.now().year
        logger.info(f"Syncing Senate disclosures for {year}...")

        disclosures = self.senate.search_all_disclosures(year)
        synced = 0
        unmatched = 0
        ambiguous = 0

        for d in disclosures:
            try:
                # Check if disclosure exists
                existing = (
                    db.query(Disclosure).filter(Disclosure.document_id == d["document_id"]).first()
                )

                if existing:
                    continue

                # Disclosure.member_id is non-nullable, so an unmatched filing
                # cannot be stored. Senate search rows carry no state, so we
                # match on name + chamber and require a unique hit rather than
                # silently attaching a filing to the wrong senator.
                last_name = (d.get("last_name") or "").strip()
                first_name = (d.get("first_name") or "").strip()
                if not last_name:
                    unmatched += 1
                    logger.warning(
                        f"Senate disclosure {d.get('document_id')} has no filer name; skipping"
                    )
                    continue

                matches = (
                    db.query(Member)
                    .filter(
                        Member.last_name.ilike(last_name),
                        Member.first_name.ilike(f"{first_name}%"),
                        Member.chamber == Chamber.SENATE,
                    )
                    .limit(2)
                    .all()
                )

                if not matches:
                    unmatched += 1
                    logger.warning(
                        f"Member not found for Senate disclosure: {first_name} {last_name}"
                    )
                    continue

                if len(matches) > 1:
                    ambiguous += 1
                    logger.warning(
                        f"Ambiguous member match for Senate disclosure: "
                        f"{first_name} {last_name} matched {len(matches)} senators; skipping"
                    )
                    continue

                db.add(
                    Disclosure(
                        member_id=matches[0].id,
                        filing_year=d["filing_year"],
                        filing_type=d.get("filing_type", "Unknown"),
                        filing_date=choose_filing_date(d.get("filing_date"), d.get("filing_year")),
                        document_id=d["document_id"],
                        document_url=d["document_url"],
                        parsed=False,
                    )
                )
                synced += 1

            except Exception as e:
                logger.error(f"Error syncing Senate disclosure {d.get('document_id')}: {e}")

        db.commit()
        logger.info(
            f"Synced {synced} Senate disclosures for {year} "
            f"({unmatched} unmatched, {ambiguous} ambiguous, skipped)"
        )
        return synced

    def download_disclosure_pdf(self, disclosure: Disclosure, force: bool = False) -> Path | None:
        """
        Download a disclosure PDF file.

        Args:
            disclosure: Disclosure record
            force: Force re-download even if file exists

        Returns:
            Path to downloaded file, or None if failed
        """
        if not disclosure.document_url:
            logger.warning(f"No URL for disclosure {disclosure.document_id}")
            return None

        # Determine path based on disclosure type
        if disclosure.is_ptr:
            pdf_dir = self.disclosures_dir / "ptr" / str(disclosure.filing_year)
        else:
            pdf_dir = self.disclosures_dir / "fd" / str(disclosure.filing_year)

        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"{disclosure.document_id}.pdf"

        # Skip if already downloaded
        if pdf_path.exists() and not force:
            return pdf_path

        urls_to_try = [disclosure.document_url]
        base_url = "https://disclosures-clerk.house.gov"

        if disclosure.is_ptr:
            ptr_url = f"{base_url}/public_disc/ptr-pdfs/{disclosure.filing_year}/{disclosure.document_id}.pdf"
            if ptr_url not in urls_to_try:
                urls_to_try.append(ptr_url)
            if "/financial-pdfs/" in disclosure.document_url:
                swapped = disclosure.document_url.replace("/financial-pdfs/", "/ptr-pdfs/")
                if swapped not in urls_to_try:
                    urls_to_try.append(swapped)
        else:
            fd_url = f"{base_url}/public_disc/financial-pdfs/{disclosure.filing_year}/{disclosure.document_id}.pdf"
            if fd_url not in urls_to_try:
                urls_to_try.append(fd_url)
            if "/ptr-pdfs/" in disclosure.document_url:
                swapped = disclosure.document_url.replace("/ptr-pdfs/", "/financial-pdfs/")
                if swapped not in urls_to_try:
                    urls_to_try.append(swapped)

        alt_url = self._build_alt_pdf_url(disclosure)
        if alt_url and alt_url not in urls_to_try:
            urls_to_try.append(alt_url)

        last_error = None
        for url in urls_to_try:
            try:
                response = requests.get(url, timeout=30)
                if response.status_code == 404:
                    last_error = f"404 Not Found: {url}"
                    continue
                response.raise_for_status()

                with open(pdf_path, "wb") as f:
                    f.write(response.content)

                if url != disclosure.document_url:
                    disclosure.document_url = url
                logger.debug(f"Downloaded {pdf_path}")
                return pdf_path

            except Exception as e:
                last_error = str(e)
                continue

        reason = last_error or "Failed to download PDF"
        disclosure.parse_error = reason
        self._record_failed_download(disclosure, reason)
        return None

    def _build_alt_pdf_url(self, disclosure: Disclosure) -> str | None:
        return _helpers.build_alt_pdf_url(disclosure)

    def _record_failed_download(self, disclosure: Disclosure, reason: str) -> str:
        return _helpers.record_failed_download(self.data_dir, disclosure, reason)

    def parse_disclosure(
        self, db: Session, disclosure: Disclosure, pdf_path: Path | None = None
    ) -> bool:
        """
        Parse a disclosure PDF and store extracted data.

        Args:
            db: Database session
            disclosure: Disclosure record to parse
            pdf_path: Path to PDF file (will download if not provided)

        Returns:
            True if parsing succeeded
        """
        # Download if needed
        if not pdf_path:
            pdf_path = self.download_disclosure_pdf(disclosure)

        if not pdf_path or not pdf_path.exists():
            disclosure.parse_error = disclosure.parse_error or "Failed to download PDF"
            db.commit()
            return False

        try:
            # Use appropriate parser based on disclosure type
            if disclosure.is_ptr:
                parsed = self.ptr_parser.parse_ptr(str(pdf_path))
                self._store_ptr_data(db, disclosure, parsed)
                text_extracted = bool((parsed.get("quality") or {}).get("text_extracted"))
                score = score_ptr_parse(
                    parsed.get("quality") or {},
                    parsed.get("transactions") or [],
                    disclosure.filing_date,
                )
            else:
                parsed = self.disclosure_parser.parse_pdf(str(pdf_path))
                self._store_fd_data(db, disclosure, parsed)
                text_extracted = bool(
                    parsed.get("raw_text") or parsed.get("assets") or parsed.get("liabilities")
                )
                score = score_fd_parse(
                    text_extracted,
                    len(parsed.get("assets") or []),
                    len(parsed.get("liabilities") or []),
                    parsed.get("parse_errors") or [],
                )

            # `parsed` means the parser ran, which is all it has ever meant. The
            # score is what says whether it worked.
            disclosure.parsed = True
            disclosure.parse_error = None
            disclosure.parse_confidence = score.confidence
            disclosure.parse_warnings = score.summary
            # A property of the document, not of the parse. Recorded so that a
            # scan and a genuine parser failure stop counting as the same thing.
            disclosure.has_text_layer = text_extracted

            if parsed.get("parse_errors"):
                disclosure.parse_error = "; ".join(parsed["parse_errors"])
            elif score.confidence == 0.0:
                # A filing that yielded nothing used to be recorded as a clean
                # success. Put it where the existing "needs attention" query
                # already looks.
                disclosure.parse_error = score.summary or "Parser extracted nothing"

            db.commit()
            logger.info(
                "Parsed disclosure %s (confidence %.2f)",
                disclosure.document_id,
                score.confidence,
            )
            return True

        except Exception as e:
            logger.error(f"Error parsing disclosure {disclosure.document_id}: {e}")
            disclosure.parse_error = str(e)
            db.commit()
            return False

    def _store_ptr_data(self, db: Session, disclosure: Disclosure, parsed: Dict[str, Any]) -> None:
        """Store parsed PTR data as Transaction records."""
        for txn_data in parsed.get("transactions", []):
            # Map transaction type
            txn_type_str = txn_data.get("transaction_type", "").lower()
            if txn_type_str == "purchase":
                txn_type = TransactionType.PURCHASE
            elif txn_type_str == "sale":
                txn_type = TransactionType.SALE
            elif txn_type_str == "exchange":
                txn_type = TransactionType.EXCHANGE
            else:
                continue  # Skip unknown transaction types

            transaction = Transaction(
                disclosure_id=disclosure.id,
                transaction_date=choose_transaction_date(
                    txn_data.get("transaction_date"),
                    disclosure.filing_date,
                ),
                transaction_type=txn_type,
                description=txn_data.get("description", ""),
                ticker=txn_data.get("ticker"),
                amount_min=txn_data.get("amount_min"),
                amount_max=txn_data.get("amount_max"),
                owner=txn_data.get("owner", "Self"),
            )
            db.add(transaction)

    def _store_fd_data(self, db: Session, disclosure: Disclosure, parsed: Dict[str, Any]) -> None:
        """Store parsed annual disclosure data."""
        # Store assets
        for asset_data in parsed.get("assets", []):
            # Map asset type
            asset_type_str = asset_data.get("asset_type", "other").lower()
            asset_type_map = {
                "stock": AssetType.STOCK,
                "bond": AssetType.BOND,
                "mutual_fund": AssetType.MUTUAL_FUND,
                "real_estate": AssetType.REAL_ESTATE,
                "retirement": AssetType.RETIREMENT,
                "bank_account": AssetType.BANK_ACCOUNT,
                "other": AssetType.OTHER,
            }
            asset_type = asset_type_map.get(asset_type_str, AssetType.OTHER)

            asset = Asset(
                disclosure_id=disclosure.id,
                asset_type=asset_type,
                description=asset_data.get("description", ""),
                ticker=asset_data.get("ticker"),
                value_min=asset_data.get("value_min"),
                value_max=asset_data.get("value_max"),
                income_min=asset_data.get("income_min"),
                income_max=asset_data.get("income_max"),
            )
            db.add(asset)

        # Store transactions (from Schedule B)
        for txn_data in parsed.get("transactions", []):
            txn_type_str = txn_data.get("transaction_type", "").lower()
            if txn_type_str == "purchase":
                txn_type = TransactionType.PURCHASE
            elif txn_type_str == "sale":
                txn_type = TransactionType.SALE
            elif txn_type_str == "exchange":
                txn_type = TransactionType.EXCHANGE
            else:
                continue

            transaction = Transaction(
                disclosure_id=disclosure.id,
                transaction_date=choose_transaction_date(
                    txn_data.get("transaction_date"),
                    disclosure.filing_date,
                ),
                transaction_type=txn_type,
                description=txn_data.get("description", ""),
                ticker=txn_data.get("ticker"),
                amount_min=txn_data.get("amount_min"),
                amount_max=txn_data.get("amount_max"),
                owner=txn_data.get("owner"),
            )
            db.add(transaction)

        # Store liabilities
        for liab_data in parsed.get("liabilities", []):
            liability = Liability(
                disclosure_id=disclosure.id,
                creditor=liab_data.get("creditor", ""),
                description=liab_data.get("description", ""),
                amount_min=liab_data.get("amount_min"),
                amount_max=liab_data.get("amount_max"),
            )
            db.add(liability)

    def parse_disclosures(
        self,
        db: Session,
        limit: int | None = None,
        member_id: int | None = None,
        year: int | None = None,
        ptr_only: bool = False,
        reparse: bool = False,
        failed_only: bool = False,
        min_confidence: float | None = None,
        delay: float = 1.0,
    ) -> Dict[str, int]:
        """
        Parse multiple unparsed disclosures.

        Args:
            db: Database session
            limit: Maximum number to parse
            member_id: Filter to specific member
            year: Filter to specific year
            ptr_only: Only parse PTR disclosures
            reparse: Re-parse already parsed disclosures
            failed_only: Only retry disclosures that failed to download
            min_confidence: Re-parse filings the parser read worse than this
            delay: Delay between downloads (seconds)

        Returns:
            Dict with counts of parsed/failed
        """
        query = db.query(Disclosure)

        if failed_only:
            query = query.filter(
                or_(
                    Disclosure.parse_error.ilike("%Failed to download%"),
                    Disclosure.parse_error.ilike("%404%"),
                )
            )
        elif min_confidence is not None:
            # Re-read the filings the parser did badly on. Unscored filings are
            # included: they were parsed before scoring existed, so nobody knows
            # how well they were read.
            query = query.filter(
                or_(
                    Disclosure.parse_confidence.is_(None),
                    Disclosure.parse_confidence < min_confidence,
                )
            )
            # Except the scans. They score 0.0 and always will: there is no
            # text in them to read. Without this, every re-parse run downloads
            # and re-reads 12.7% of the House corpus to reach the same answer
            # it reached last time. `--reparse` still reaches them; filings
            # whose text layer is unknown are still included, because nobody
            # has checked those.
            query = query.filter(
                or_(
                    Disclosure.has_text_layer.is_(None),
                    Disclosure.has_text_layer.is_(True),
                )
            )
        elif not reparse:
            query = query.filter(Disclosure.parsed == False)

        if member_id:
            query = query.filter(Disclosure.member_id == member_id)

        if year:
            query = query.filter(Disclosure.filing_year == year)

        if ptr_only:
            query = query.filter(Disclosure.is_ptr == True)

        # Least-recently-touched first, and this is what makes `--limit`
        # resumable rather than a treadmill.
        #
        # There was no ordering at all, so the database returned an arbitrary
        # set. That is harmless for the default filter -- a filing that parses
        # leaves `parsed == False` and cannot come back -- but it silently
        # breaks the two filters a filing can stay inside after being read.
        # `--min-confidence 1.0 --limit 500` would take some arbitrary 500,
        # re-read them, leave any that scored below 1.0 still matching, and
        # take the same 500 again on the next run: a re-parse campaign that
        # never reaches the rest of the corpus however many times it is run.
        #
        # `updated_at` carries `onupdate`, so parsing a filing moves it to the
        # back of the queue whatever its new score. Each run therefore advances
        # to filings it has not reached, and repeated runs converge.
        query = query.order_by(Disclosure.updated_at.asc(), Disclosure.id.asc())

        if limit:
            query = query.limit(limit)

        disclosures = query.all()
        logger.info(f"Found {len(disclosures)} disclosures to parse")

        results = {"parsed": 0, "failed": 0, "skipped": 0}

        for disclosure in disclosures:
            try:
                success = self.parse_disclosure(db, disclosure)
                if success:
                    results["parsed"] += 1
                else:
                    results["failed"] += 1

                # Rate limiting
                if delay > 0:
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"Error processing {disclosure.document_id}: {e}")
                results["failed"] += 1

        logger.info(f"Parsing complete: {results}")
        return results

    def run_full_sync(
        self,
        years: List[int] | None = None,
        download_files: bool = False,
        include_ptrs: bool = True,
    ) -> Dict[str, int]:
        """
        Run a full synchronization of all data sources.

        Args:
            years: List of years to sync (defaults to current year)
            download_files: Whether to download disclosure files
            include_ptrs: Whether to include Periodic Transaction Reports

        Returns:
            Summary of synced items
        """
        years = years or [datetime.now().year]

        summary = {
            "members": 0,
            "house_disclosures": 0,
            "house_ptrs": 0,
            "senate_disclosures": 0,
        }

        with get_db() as db:
            # Sync members first
            summary["members"] = self.sync_members(db)

            # Sync disclosures for each year
            for year in years:
                summary["house_disclosures"] += self.sync_house_disclosures(
                    db, year, download_files
                )

                # Sync PTRs (stock trades)
                if include_ptrs:
                    summary["house_ptrs"] += self.sync_house_ptrs(db, year, download_files)

                summary["senate_disclosures"] += self.sync_senate_disclosures(
                    db, year, download_files
                )

        logger.info(f"Full sync complete: {summary}")
        return summary

    def fix_future_dates(self, db: Session) -> int:
        """Fix any disclosure records with future filing dates.

        Delegates to the standalone helper; the wrapper remains for
        backward compatibility with existing callers (CLI, API, tests).
        """
        return _helpers.fix_future_dates(db)

    def download_all_pdfs(
        self, db: Session, limit: int | None = None, force: bool = False, delay: float = 0.5
    ) -> Dict[str, int]:
        """
        Download PDFs for all disclosures that have URLs.
        Creates a paper trail by storing local copies.

        Args:
            db: Database session
            limit: Max number to download
            force: Re-download even if file exists
            delay: Delay between downloads (seconds)

        Returns:
            Summary of download results
        """
        import time

        # First fix any future dates
        self.fix_future_dates(db)

        # Get disclosures that need PDFs
        query = db.query(Disclosure).filter(
            Disclosure.document_url.isnot(None),
            Disclosure.document_url != "",
        )

        if limit:
            query = query.limit(limit)

        disclosures = query.all()
        logger.info(f"Found {len(disclosures)} disclosures with URLs to download")

        results = {
            "downloaded": 0,
            "already_exists": 0,
            "failed": 0,
        }

        for disclosure in disclosures:
            # Determine save path
            if disclosure.is_ptr:
                pdf_dir = self.disclosures_dir / "ptr" / str(disclosure.filing_year)
            else:
                pdf_dir = self.disclosures_dir / "fd" / str(disclosure.filing_year)

            pdf_path = pdf_dir / f"{disclosure.document_id}.pdf"

            # Skip if already exists
            if pdf_path.exists() and not force:
                results["already_exists"] += 1
                continue

            # Try to download
            downloaded_path = self.download_disclosure_pdf(disclosure, force=force)

            if downloaded_path:
                results["downloaded"] += 1
            else:
                results["failed"] += 1

            time.sleep(delay)

        db.commit()
        logger.info(f"Download complete: {results}")
        return results


def run_ingestion(
    years: List[int] | None = None, download_files: bool = False, include_ptrs: bool = True
) -> Dict[str, int]:
    """
    Convenience function to run ingestion.

    Args:
        years: List of years to sync
        download_files: Whether to download disclosure files
        include_ptrs: Whether to include Periodic Transaction Reports

    Returns:
        Summary of synced items
    """
    orchestrator = IngestionOrchestrator()
    return orchestrator.run_full_sync(years, download_files, include_ptrs)
