#!/usr/bin/env python
"""Command-line interface for Honest Congress."""

import argparse
import logging
import sys
from datetime import datetime

from src.analysis import analyze_wealth
from src.analysis.baselines import annotate_percentile_ranks, detection_summary
from src.db import get_db
from src.db.utils import recalculate_member_counts
from src.ingestion import run_ingestion


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def cmd_init(args):
    """Initialize / migrate the database.

    Runs Alembic to upgrade the configured database to the latest revision.
    Equivalent to `alembic upgrade head` and safe to run repeatedly.
    """
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    repo_root = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    print("Running database migrations (alembic upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    print("Database is up to date.")


def cmd_ingest(args):
    """Run data ingestion."""
    years = args.years or [datetime.now().year]
    include_ptrs = not args.no_ptr

    print(f"Ingesting data for years: {years}")
    if include_ptrs:
        print("Including Periodic Transaction Reports (stock trades)")

    summary = run_ingestion(years=years, download_files=args.download, include_ptrs=include_ptrs)

    # Member.disclosure_count is denormalized and the members API sorts and
    # filters on it, so it has to be refreshed whenever disclosures change.
    with get_db() as db:
        recalculate_member_counts(db)

    print("\nIngestion complete:")
    print(f"  Members synced: {summary['members']}")
    print(f"  House disclosures: {summary['house_disclosures']}")
    print(f"  House PTRs (stock trades): {summary['house_ptrs']}")
    print(f"  Senate disclosures: {summary['senate_disclosures']}")

    # Printed only when there is something to say, but never hidden. A filing
    # whose filer matched no member is not stored and never reaches the site;
    # before this it was dropped behind a debug log and nothing counted it.
    if summary.get("senate_unavailable"):
        print(
            "  WARNING: Senate eFD could not be queried. The Senate count above is an "
            "outage, not a finding about the Senate."
        )

    unmatched = summary.get("unmatched_filers", 0)
    if unmatched:
        print(
            f"  NOT stored — filer matched no member: {unmatched} "
            "(see the warnings above for the breakdown by filing type)"
        )


def cmd_analyze(args):
    """Run anomaly analysis."""
    from src.analysis import (
        analyze_trades,
        run_advanced_anomaly_detection,
        run_cluster_detection,
        run_committee_conflict_detection,
        run_extended_anomaly_detection,
        run_tier2_detection,
    )
    from src.analysis.legislation import run_legislation_detection
    from src.analysis.significance import annotate_significance
    from src.config import get_settings

    analysis_types = []

    if args.type in ("all", "wealth"):
        analysis_types.append(("wealth", analyze_wealth))
    if args.type in ("all", "trades"):
        analysis_types.append(("trades", analyze_trades))

    total_anomalies = 0

    for name, analyzer in analysis_types:
        print(f"Running {name} analysis...")

        with get_db() as db:
            result = analyzer(db, member_id=args.member_id)

        print(f"\n{name.title()} Analysis Results:")
        if args.member_id:
            print(f"  Member ID: {result['member_id']}")
            print(f"  Anomalies found: {result['total_anomalies']}")
        else:
            print(f"  Members analyzed: {result.get('members_analyzed', 'N/A')}")
            print(f"  Members with anomalies: {result.get('members_with_anomalies', 'N/A')}")
            print(f"  Total anomalies: {result['total_anomalies']}")

        total_anomalies += result["total_anomalies"]

        if args.verbose and result.get("anomalies"):
            print("\n  Top anomalies:")
            for a in result["anomalies"][:5]:
                print(f"    - [{a.get('severity', '?')}] {a['title']}")

    # Advanced + extended detectors run only on the full (all-members) pass.
    # They scan cross-member patterns that don't make sense per-member.
    if args.type in ("all", "advanced") and not args.member_id:
        print("\nRunning advanced + extended detection...")
        with get_db() as db:
            advanced = run_advanced_anomaly_detection(db)
            extended = run_extended_anomaly_detection(db, advanced)

        print("\nAdvanced Analysis Results:")
        print(f"  Wealth/Salary: {len(advanced.get('wealth_anomalies', []))}")
        print(f"  Asset Appreciation: {len(advanced.get('asset_anomalies', []))}")
        print(f"  Stock Outperformance: {len(advanced.get('stock_anomalies', []))}")

        print("\nRunning committee jurisdiction conflict detection...")
        with get_db() as db:
            committee = run_committee_conflict_detection(db)
        print(f"  Committee jurisdiction conflicts: {committee['total']}")
        total_anomalies += committee["total"]

        print("\nRunning cross-member cluster detection...")
        with get_db() as db:
            clusters = run_cluster_detection(db)
        print(f"  Cross-member clusters: {clusters['total']}")
        total_anomalies += clusters["total"]

        # The Tier-2 detectors were reachable only from the API, so the nightly
        # workflow -- which runs this command -- never ran them at all. Their
        # three source tables are fed by `ingest-contracts`, `ingest-donations`
        # and `ingest-lobbying`; `stats` reports any that are still empty.
        # The join to legislative action -- what the member did in office, not
        # just what they traded. Reads bills/sponsorships/referrals from
        # `ingest-bills`; `stats` reports if those tables are empty.
        print("\nRunning legislative action detection...")
        with get_db() as db:
            legislation = run_legislation_detection(db)
        print(f"  Sponsorship conflicts: {len(legislation['sponsorship_conflicts'])}")
        print(f"  Bill jurisdiction conflicts: {len(legislation['bill_jurisdiction_conflicts'])}")
        total_anomalies += legislation["total"]

        print("\nRunning donor / lobbying / contract detection...")
        with get_db() as db:
            tier2 = run_tier2_detection(db)
        print(f"  Donor conflicts: {len(tier2.get('donor_anomalies', []))}")
        print(f"  Lobbying overlaps: {len(tier2.get('lobbying_anomalies', []))}")
        print(f"  Contract front-runs: {len(tier2.get('contract_anomalies', []))}")
        total_anomalies += tier2.get("total", 0)

        print("\nExtended Analysis Results:")
        print(f"  Trade Timing: {len(extended.get('timing_anomalies', []))}")
        print(f"  Committee Conflicts: {len(extended.get('conflict_anomalies', []))}")
        print(f"  Loss Avoidance: {len(extended.get('loss_avoidance_anomalies', []))}")
        print(f"  Multi-Factor Risk: {len(extended.get('combination_anomalies', []))}")

        total_anomalies += advanced.get("total", 0) + extended.get("total", 0)

    # Multiple-comparisons control, before the percentile ranks: the suite has
    # just run thousands of tests over hundreds of people, and some of what it
    # found is what that produces. Only the timing-coincidence detectors admit
    # a null model; the rest are explicitly left without one.
    with get_db() as db:
        significance = annotate_significance(
            db,
            permutations=get_settings().significance_permutations,
            alpha=get_settings().fdr_alpha,
        )
    print(
        f"\nSignificance: {significance['tests']} tests, "
        f"{significance['tests_passing_fdr']} passing FDR at alpha="
        f"{significance['alpha']}"
    )
    print(
        f"  Findings with a null model: {significance['findings_annotated']}; "
        f"without one: {significance['findings_without_a_null_model']}"
    )

    # Member.anomaly_count is denormalized; refresh it now that anomalies moved.
    # Percentile ranks compare each finding against others of its own type and
    # must be recomputed whenever the population changes.
    with get_db() as db:
        recalculate_member_counts(db)
        ranks = annotate_percentile_ranks(db)
        summary = detection_summary(db)

    print(f"\nTotal anomalies detected: {total_anomalies}")
    print(f"Percentile ranks assigned: {ranks['ranked']}")
    if ranks["skipped_small_population"]:
        print(
            f"  ({ranks['skipped_small_population']} left unranked - "
            f"too few findings of their type to rank against)"
        )
    print(
        f"\nContext: ~{summary['member_detector_pairs']} detector-member pairs produced "
        f"{summary['total_findings']} findings across {summary['members']} members."
    )


def cmd_parse(args):
    """Parse disclosure PDFs."""
    from src.ingestion.orchestrator import IngestionOrchestrator

    print("Parsing disclosure PDFs...")

    orchestrator = IngestionOrchestrator()

    with get_db() as db:
        result = orchestrator.parse_disclosures(
            db,
            limit=args.limit,
            member_id=args.member_id,
            year=args.year,
            ptr_only=args.ptr_only,
            reparse=args.reparse,
            failed_only=args.failed_only,
            min_confidence=args.min_confidence,
            delay=args.delay,
        )

    print("\nParsing complete:")
    print(f"  Successfully parsed: {result['parsed']}")
    print(f"  Failed: {result['failed']}")
    print(f"  Skipped: {result['skipped']}")

    from src.analysis.baselines import parse_quality_summary

    with get_db() as db:
        quality = parse_quality_summary(db)
    print("\nHow well they were read:")
    print(f"  Mean confidence: {quality['mean_confidence']}")
    print(f"  Below 0.8: {quality['filings_below_0_8']}")
    print(
        f"  Yielded nothing at all: {quality['filings_that_yielded_nothing']}"
        "  - a PTR with no transactions is a failed parse, not a quiet quarter"
    )
    print(
        f"  Scans with no text layer: {quality['filings_with_no_text_layer']}"
        "  - not a parse failure; there is nothing in them to read"
    )


def cmd_download_pdfs(args):
    """Download PDFs for all disclosures."""
    from src.ingestion.orchestrator import IngestionOrchestrator

    print("Downloading disclosure PDFs...")
    print("This creates a paper trail by storing local copies of official documents.\n")

    orchestrator = IngestionOrchestrator()

    with get_db() as db:
        # First fix any future dates
        fixed = orchestrator.fix_future_dates(db)
        if fixed > 0:
            print(f"Fixed {fixed} disclosures with future dates.\n")

        result = orchestrator.download_all_pdfs(
            db,
            limit=args.limit,
            force=args.force,
            delay=args.delay,
        )

    print("\nDownload complete:")
    print(f"  Downloaded: {result['downloaded']}")
    print(f"  Already exists: {result['already_exists']}")
    print(f"  Failed: {result['failed']}")
    print("\nPDFs stored in: data/disclosures/")


def cmd_fix_dates(args):
    """Fix future dates in database records."""
    from src.ingestion.orchestrator import IngestionOrchestrator

    print("Checking for disclosures with future dates...")

    orchestrator = IngestionOrchestrator()

    with get_db() as db:
        fixed = orchestrator.fix_future_dates(db)

    if fixed > 0:
        print(f"\nFixed {fixed} disclosures with future dates.")
    else:
        print("\nNo disclosures with future dates found.")


def cmd_fix_urls(args):
    """Fix incorrect disclosure URLs in the database.

    HOUSE ONLY, and the filter is load-bearing rather than tidy. Every URL this
    builds points at disclosures-clerk.house.gov, and a House Clerk URL is
    reconstructible because the document id IS the filename. A Senate filing is
    neither: it lives at efdsearch.senate.gov under a UUID and a path segment
    that varies by format (/view/ptr/, /view/paper/, /view/annual/), none of
    which can be derived from what is stored. Run unscoped against a database
    containing Senate rows -- which it now can, since Senate ingestion actually
    retrieves filings -- this would rewrite every Senate URL into a House Clerk
    URL that 404s, discarding the only link to the real document.
    """
    from src.db.models import Chamber, Disclosure, Member

    BASE_URL = "https://disclosures-clerk.house.gov/public_disc"

    def get_correct_url(d):
        if d.is_ptr:
            return f"{BASE_URL}/ptr-pdfs/{d.filing_year}/{d.document_id}.pdf"
        return f"{BASE_URL}/financial-pdfs/{d.filing_year}/{d.document_id}.pdf"

    print("Checking House disclosure URLs...")

    with get_db() as db:
        disclosures = (
            db.query(Disclosure)
            .join(Member, Disclosure.member_id == Member.id)
            .filter(Member.chamber == Chamber.HOUSE)
            .all()
        )

        skipped = (
            db.query(Disclosure)
            .join(Member, Disclosure.member_id == Member.id)
            .filter(Member.chamber != Chamber.HOUSE)
            .count()
        )
        if skipped:
            print(f"  (skipping {skipped} non-House disclosures — their URLs are not derivable)")

        issues = []
        for d in disclosures:
            correct = get_correct_url(d)
            current = d.document_url or ""
            if current != correct:
                issues.append((d, correct))

        print(f"Found {len(issues)} URLs needing fixes out of {len(disclosures)} disclosures")

        if issues and not args.dry_run:
            for d, correct in issues:
                d.document_url = correct
            db.commit()
            print(f"✓ Fixed {len(issues)} URLs")
        elif issues:
            print("\nDry run - no changes made. Use without --dry-run to apply fixes.")
            for d, correct in issues[:5]:
                print(f"  {d.document_id}: {d.document_url} -> {correct}")
            if len(issues) > 5:
                print(f"  ... and {len(issues) - 5} more")


def cmd_parse_fd(args):
    """Parse assets and income sources out of annual FD filings.

    These parsers were previously reachable only from a one-off script in
    scripts/, which is why neither had a documented entry point.
    """
    from src.parsing.fd_asset_parser import FDAssetParser
    from src.parsing.fd_income_parser import FDIncomeParser

    if args.income_only and args.assets_only:
        print("--assets-only and --income-only are mutually exclusive.")
        sys.exit(1)

    if not args.income_only:
        print("Parsing FD assets...")
        FDAssetParser().parse_all_disclosures()

    if not args.assets_only:
        print("Parsing FD income sources...")
        FDIncomeParser().parse_all_disclosures()

    with get_db() as db:
        recalculate_member_counts(db)

    print("FD parsing complete.")


def cmd_sync_committees(args):
    """Fetch current committee assignments from congress-legislators."""
    from src.ingestion.committees import ingest_committee_assignments

    print("Syncing committee assignments...")

    with get_db() as db:
        result = ingest_committee_assignments(db)

    print("\nCommittee sync complete:")
    print(f"  Committees indexed: {result['committees_indexed']}")
    print(f"  Assignments stored: {result['assignments']}")
    if result["skipped_unknown_member"]:
        print(
            f"  Skipped (member not in database): {result['skipped_unknown_member']}"
            "  - run `ingest` first to populate the roster"
        )
    if result["skipped_unknown_committee"]:
        print(f"  Skipped (committee not in metadata): {result['skipped_unknown_committee']}")


def cmd_ingest_contracts(args):
    """Ingest federal contract awards from USASpending."""
    from src.ingestion.usaspending import ingest_government_contracts

    print(f"Ingesting federal contract awards ({args.start} to {args.end or 'today'})...")
    print("Asking USASpending about the companies members have actually traded.")

    with get_db() as db:
        result = ingest_government_contracts(
            db, start_date=args.start, end_date=args.end, pages=args.pages_per_company
        )

    if not result["tickers_queried"] and not result["tickers_without_a_registered_name"]:
        print(
            "\nNo transaction carries a ticker, so there was nothing to ask about. "
            "Run `ingest` and `parse` first."
        )
        return

    print("\nContract ingestion complete:")
    print(f"  Companies asked about: {result['tickers_queried']}")
    print(f"  Award actions fetched: {result['fetched']}")
    print(f"  Imported: {result['imported']}")
    print(f"  Already present: {result['duplicates']}")
    print(
        f"  Tickers not in the SEC register: {result['tickers_without_a_registered_name']}"
        "  - foreign listings, funds, and misread symbols"
    )
    # Not folded into the line above. These are awards USASpending matched to
    # the company through its own recipient hierarchy, which the SEC register
    # cannot confirm -- a known gap in coverage, and the numbers say how big.
    print(f"  Rejected as a different company: {result['rejected_wrong_company']}")
    top = sorted(result["rejected_names"].items(), key=lambda kv: -kv[1])[:10]
    for name, count in top:
        print(f"      {count:>5}  {name}")


def cmd_ingest_donations(args):
    """Ingest corporate PAC donations from the FEC."""
    from src.config import get_settings
    from src.ingestion.fec import ingest_campaign_donations

    api_key = get_settings().fec_api_key
    if not api_key:
        print("FEC_API_KEY is not set. Get a free key at https://api.data.gov/signup/")
        sys.exit(1)

    print(f"Ingesting corporate PAC donations for the {args.cycle} cycle...")
    if args.all_pacs:
        print("  (scanning every corporate PAC, not just those members have traded)")

    with get_db() as db:
        result = ingest_campaign_donations(
            db,
            api_key,
            cycle=args.cycle,
            max_requests=args.max_requests,
            restrict_to_traded=not args.all_pacs,
            resume=not args.no_resume,
        )

    print("\nDonation ingestion complete:")
    print(f"  PACs queried: {result['pacs_queried']}")
    if result["pacs_already_ingested"]:
        print(f"  PACs already ingested (skipped): {result['pacs_already_ingested']}")
    print(f"  Imported: {result['imported']}")
    print(f"  Already present: {result['duplicates']}")
    print(
        f"  Receipts to committees with no sitting member: {result['skipped_unmapped_recipient']}"
    )
    print(f"  FEC requests used: {result['requests_made']}")
    if result["stopped_early"]:
        print(
            "\n  Stopped at the request cap. Nothing is lost - rerun the same "
            "command and it resumes from the PACs it has not reached yet."
        )


def cmd_significance(args):
    """Recompute p-values and FDR q-values over existing findings."""
    from src.analysis.significance import annotate_significance
    from src.config import get_settings

    settings = get_settings()
    alpha = args.alpha if args.alpha is not None else settings.fdr_alpha
    permutations = args.permutations or settings.significance_permutations

    print(f"Testing findings against a shifted-calendar null ({permutations} permutations)...")

    with get_db() as db:
        result = annotate_significance(db, permutations=permutations, alpha=alpha, seed=args.seed)

    print("\nSignificance complete:")
    print(f"  Tests run: {result['tests']}")
    print(f"  Passing FDR at alpha={result['alpha']}: {result['tests_passing_fdr']}")
    print(f"  Expected false discoveries among those: {result['expected_false_discoveries']}")
    print(f"  Findings annotated: {result['findings_annotated']}")
    print(
        f"  Findings with no null model: {result['findings_without_a_null_model']}"
        "  - magnitude rules; they carry percentile_rank instead"
    )


def cmd_sync_industries(args):
    """Cache SEC industry codes for traded tickers."""
    from src.ingestion.sec_industries import ingest_company_industries

    scope = "every SEC registrant" if args.all else "tickers members have traded"
    print(f"Looking up SEC industry codes for {scope}...")

    with get_db() as db:
        result = ingest_company_industries(
            db, all_registrants=args.all, max_requests=args.max_requests
        )

    print("\nIndustry lookup complete:")
    print(f"  Looked up: {result['looked_up']}")
    print(
        f"  Symbols that are not SEC registrants: {result['not_sec_registrants']}"
        "  - funds, foreign listings, or symbols the PDF parser misread"
    )
    print(
        f"  Tickers now carrying a sector: {result['tickers_with_a_sector']}"
        f" of {result['cached_tickers']} cached"
    )
    print(f"  SEC requests used: {result['requests_made']}")
    if result["stopped_early"]:
        print("\n  Stopped at the request cap. Rerun to continue where it left off.")


def cmd_ingest_bills(args):
    """Ingest bill sponsorship and committee referrals from Congress.gov."""
    from src.analysis.legislation import bills_worth_committee_lookup, coverage_report
    from src.config import get_settings
    from src.ingestion.bills import fetch_bill_committees, ingest_member_bills

    api_key = get_settings().congress_gov_api_key
    if not api_key:
        print(
            "CONGRESS_GOV_API_KEY is not set. Get a free key at https://api.congress.gov/sign-up/"
        )
        sys.exit(1)

    print("Ingesting bill sponsorship from Congress.gov...")

    with get_db() as db:
        result = ingest_member_bills(
            db,
            api_key,
            max_requests=args.max_requests,
            include_cosponsored=not args.sponsored_only,
        )

    print("\nSponsorship ingestion complete:")
    print(f"  Members queried: {result['members_queried']}")
    print(f"  Bills stored: {result['bills']}")
    print(f"  Sponsorships: {result['sponsorships']}")
    print(f"  Cosponsorships: {result['cosponsorships']}")
    print(f"  Congress.gov requests used: {result['requests_made']}")

    if not args.skip_committees:
        # One request per bill, so only for bills that could actually produce a
        # finding: a sector-mapped policy area and a trade by a member who
        # touched the bill. On real data this is a ~99% reduction.
        print("\nFetching committee referrals for bills that matched a member's trading...")
        with get_db() as db:
            candidates = bills_worth_committee_lookup(db)
            referrals = fetch_bill_committees(db, api_key, bills=candidates)
        print(f"  Bills looked up: {referrals['bills_looked_up']}")
        print(f"  Referrals stored: {referrals['referrals']}")
        print(f"  Congress.gov requests used: {referrals['requests_made']}")

    with get_db() as db:
        coverage = coverage_report(db)

    print("\nWhat the legislative detectors can see:")
    print(
        f"  Bills in a sector-mapped policy area: {coverage['bills_mapped_to_a_sector']}"
        f" of {coverage['bills']}"
    )
    print(
        f"  Traded tickers with a known sector: "
        f"{coverage['traded_tickers_with_a_known_sector']}"
        f" of {coverage['distinct_traded_tickers']}"
        "  - this is the binding constraint; see src/analysis/sectors.py"
    )

    if result["stopped_early"]:
        print("\n  Stopped at the request cap. Rerun to continue.")


def cmd_ingest_lobbying(args):
    """Ingest lobbying disclosures from the Senate LDA."""
    from src.config import get_settings
    from src.ingestion.lda import ingest_lobbying_disclosures

    api_key = get_settings().lda_api_key
    if not api_key:
        print(
            "No LDA_API_KEY set - running anonymously at a lower rate limit. "
            "A free key from https://lda.senate.gov/api/register/ makes this ~8x faster."
        )

    print(f"Ingesting {args.year} lobbying disclosures for companies members have traded...")

    with get_db() as db:
        result = ingest_lobbying_disclosures(
            db, filing_year=args.year, api_key=api_key, tickers=args.tickers
        )

    print("\nLobbying ingestion complete:")
    print(f"  Tickers queried: {result['tickers_queried']}")
    print(f"  Tickers not in the SEC register: {result['tickers_without_a_registered_name']}")
    print(f"  Imported: {result['imported']}")
    print(f"  Already present: {result['duplicates']}")
    print(
        f"  Rejected as a different company: {result['rejected_wrong_company']}"
        "  - the API matches client names by substring"
    )
    print(f"  LDA requests used: {result['requests_made']}")


def cmd_compliance(args):
    """Rank members by STOCK Act filing punctuality."""
    from src.analysis.compliance import compliance_leaderboard

    with get_db() as db:
        board = compliance_leaderboard(db, min_transactions=args.min_transactions, limit=args.limit)

    print(
        f"STOCK Act filing compliance "
        f"({board['deadline_days']}-day deadline, "
        f"min {board['min_transactions']} transactions)\n"
    )
    print(
        f"{board['total_filed_late']} of {board['total_transactions_checked']} "
        f"transactions filed late ({board['overall_late_rate_percent']}%) "
        f"across {board['members_ranked']} members.\n"
    )

    if not board["members"]:
        print("No members meet the minimum transaction count.")
        return

    print(f"{'Member':<28} {'Party':<6} {'Late':>6} {'Checked':>8} {'Rate':>7} {'Worst':>7}")
    print("-" * 68)
    for entry in board["members"]:
        print(
            f"{entry['member_name'][:27]:<28} "
            f"{(entry['party'] or '')[:5]:<6} "
            f"{entry['filed_late']:>6} "
            f"{entry['transactions_checked']:>8} "
            f"{entry['late_rate_percent']:>6.1f}% "
            f"{entry['max_days_late']:>6}d"
        )


def cmd_stats(args):
    """Show detector output in context: how many tests, how many findings."""
    import json

    with get_db() as db:
        summary = detection_summary(db)

    print(json.dumps(summary, indent=2))


def _alembic_config():
    """Alembic config pointed at this repo's alembic.ini."""
    from pathlib import Path

    from alembic.config import Config

    return Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))


def cmd_reset(args):
    """Delete all data and rebuild the schema from scratch.

    Irreversible. Dry run by default -- it reports what exists and exits
    without touching anything unless --yes is given, and refuses outright in
    production unless --force-production is also given.

    The schema is dropped and rebuilt (`alembic downgrade base` then `upgrade
    head`) rather than the tables merely emptied, so nothing survives: no rows,
    no sequence state, and no drift from schema changes made outside Alembic.
    """
    import shutil
    from pathlib import Path

    from sqlalchemy import func

    from alembic import command
    from src.config import get_settings
    from src.db.models import Base

    settings = get_settings()

    # Report before destroying. Ordered as the tables will be dropped, so the
    # output reads in the same order as the work.
    tables = list(reversed(Base.metadata.sorted_tables))
    model_by_table = {
        m.__tablename__: m
        for m in Base.registry._class_registry.values()
        if hasattr(m, "__tablename__")
    }

    print(f"Target database: {settings.database_url_display}")
    print(f"Environment:     {settings.env}\n")

    total = 0
    with get_db() as db:
        print(f"{'Table':<26} {'Rows':>10}")
        print("-" * 38)
        for table in tables:
            model = model_by_table.get(table.name)
            if model is None:
                continue
            try:
                count = db.query(func.count()).select_from(table).scalar() or 0
            except Exception as exc:  # table may not exist yet
                print(f"{table.name:<26} {'(missing)':>10}  {exc.__class__.__name__}")
                continue
            total += count
            print(f"{table.name:<26} {count:>10,}")
        print("-" * 38)
        print(f"{'TOTAL':<26} {total:>10,}\n")

    disclosures_dir = Path(__file__).resolve().parent.parent / "data" / "disclosures"
    pdf_count = len(list(disclosures_dir.rglob("*.pdf"))) if disclosures_dir.exists() else 0
    if pdf_count:
        fate = "DELETED" if args.purge_pdfs else "kept (pass --purge-pdfs to remove)"
        print(f"Local PDFs: {pdf_count:,} files in {disclosures_dir} -- {fate}\n")

    if not args.yes:
        print("Dry run. Nothing has been changed.")
        print("Re-run with --yes to delete all of the above and rebuild the schema.")
        return

    if settings.is_production and not args.force_production:
        print("REFUSING: ENV=production.")
        print("This would destroy the live database. Pass --force-production if that is")
        print("genuinely what you want.")
        sys.exit(1)

    print("Dropping schema (alembic downgrade base)...")
    command.downgrade(_alembic_config(), "base")

    print("Rebuilding schema (alembic upgrade head)...")
    command.upgrade(_alembic_config(), "head")

    if args.purge_pdfs and disclosures_dir.exists():
        shutil.rmtree(disclosures_dir)
        print(f"Deleted {pdf_count:,} local PDFs.")

    print("\nReset complete. The database is empty and at the latest revision.")
    print("\nRe-ingest with:")
    print("  python -m src.cli ingest -y 2024 2025")
    print("  python -m src.cli download-pdfs")
    print("  python -m src.cli parse")
    print("  python -m src.cli sync-committees")
    print("  python -m src.cli analyze")


def cmd_recount(args):
    """Recalculate the materialized count columns on members."""
    print("Recalculating member counts...")

    with get_db() as db:
        stats = recalculate_member_counts(db)

    print(
        f"Done. {stats['members']} members, {stats['disclosures']} disclosures, "
        f"{stats['anomalies']} anomalies."
    )


def cmd_purge_disabled(args):
    """Delete persisted anomalies whose detector has since been disabled.

    Disabling a detector stops new rows, but rows written before it was
    disabled stay in the database and keep being served by the API. This
    removes them.
    """
    from src.config import get_settings
    from src.db.models import Anomaly

    disabled = sorted(get_settings().disabled_anomaly_types_set)
    if not disabled:
        print("No anomaly types are disabled; nothing to purge.")
        return

    print(f"Disabled anomaly types: {', '.join(disabled)}")

    with get_db() as db:
        rows = db.query(Anomaly).filter(Anomaly.anomaly_type.in_(disabled)).all()

        if not rows:
            print("No persisted anomalies of disabled types found.")
            return

        counts: dict[str, int] = {}
        for row in rows:
            counts[row.anomaly_type] = counts.get(row.anomaly_type, 0) + 1

        for atype, count in sorted(counts.items()):
            print(f"  {atype}: {count}")

        if args.dry_run:
            print(
                f"\nDry run - {len(rows)} anomalies would be deleted. "
                f"Re-run without --dry-run to apply."
            )
            return

        deleted = (
            db.query(Anomaly)
            .filter(Anomaly.anomaly_type.in_(disabled))
            .delete(synchronize_session=False)
        )
        db.commit()
        recalculate_member_counts(db)
        print(f"\nDeleted {deleted} anomalies of disabled types.")


def cmd_serve(args):
    """Start the API server."""
    import os

    import uvicorn

    # Honor PORT/HOST env vars when CLI flags weren't supplied — required for
    # Railway / Heroku / Fly which inject PORT.
    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "8000"))

    print(f"Starting server on {host}:{port}...")
    uvicorn.run(
        "src.api.main:app",
        host=host,
        port=port,
        reload=args.reload,
    )


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Honest Congress - Congressional Financial Disclosure Analyzer"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize database")
    init_parser.set_defaults(func=cmd_init)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest disclosure data")
    ingest_parser.add_argument(
        "-y", "--years", type=int, nargs="+", help="Years to ingest (default: current year)"
    )
    ingest_parser.add_argument("-d", "--download", action="store_true", help="Download PDF files")
    ingest_parser.add_argument(
        "--no-ptr", action="store_true", help="Skip Periodic Transaction Reports (stock trades)"
    )
    ingest_parser.set_defaults(func=cmd_ingest)

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Run anomaly analysis")
    analyze_parser.add_argument("-m", "--member-id", type=int, help="Analyze specific member by ID")
    analyze_parser.add_argument(
        "-t",
        "--type",
        choices=["all", "wealth", "trades", "advanced"],
        default="all",
        help=(
            "Type of analysis to run (default: all). 'advanced' runs the "
            "wealth-vs-salary, rapid asset appreciation, stock outperformance, "
            "trade timing, committee conflict, loss avoidance, and "
            "multi-factor risk detectors."
        ),
    )
    analyze_parser.add_argument(
        "--verbose", action="store_true", help="Show detailed anomaly information"
    )
    analyze_parser.set_defaults(func=cmd_analyze)

    # Parse command
    parse_parser = subparsers.add_parser("parse", help="Parse disclosure PDFs")
    parse_parser.add_argument(
        "-l", "--limit", type=int, help="Maximum number of disclosures to parse"
    )
    parse_parser.add_argument(
        "-m", "--member-id", type=int, help="Parse disclosures for specific member ID"
    )
    parse_parser.add_argument("-y", "--year", type=int, help="Parse disclosures for specific year")
    parse_parser.add_argument(
        "--ptr-only", action="store_true", help="Only parse PTR (stock trade) disclosures"
    )
    parse_parser.add_argument(
        "--reparse", action="store_true", help="Re-parse already parsed disclosures"
    )
    parse_parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help=(
            "Re-parse filings the parser read worse than this (0-1), and filings "
            "never scored at all. Use after improving the parser."
        ),
    )
    parse_parser.add_argument(
        "--delay", type=float, default=1.0, help="Delay between downloads in seconds (default: 1.0)"
    )
    parse_parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Only retry disclosures that previously failed to download",
    )
    parse_parser.set_defaults(func=cmd_parse)

    # Download PDFs command
    download_parser = subparsers.add_parser(
        "download-pdfs", help="Download PDFs for all disclosures (paper trail)"
    )
    download_parser.add_argument(
        "-l", "--limit", type=int, help="Maximum number of PDFs to download"
    )
    download_parser.add_argument(
        "--force", action="store_true", help="Re-download even if file already exists"
    )
    download_parser.add_argument(
        "--delay", type=float, default=0.5, help="Delay between downloads in seconds (default: 0.5)"
    )
    download_parser.set_defaults(func=cmd_download_pdfs)

    # Fix dates command
    fix_dates_parser = subparsers.add_parser(
        "fix-dates", help="Fix future dates in database records"
    )
    fix_dates_parser.set_defaults(func=cmd_fix_dates)

    # Fix URLs command
    fix_urls_parser = subparsers.add_parser(
        "fix-urls", help="Fix incorrect disclosure URLs in database"
    )
    fix_urls_parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without applying them"
    )
    fix_urls_parser.set_defaults(func=cmd_fix_urls)

    # Performance command

    # Parse FD assets / income
    parse_fd_parser = subparsers.add_parser(
        "parse-fd", help="Parse assets and income sources from annual FD filings"
    )
    parse_fd_parser.add_argument("--assets-only", action="store_true", help="Parse assets only")
    parse_fd_parser.add_argument(
        "--income-only", action="store_true", help="Parse income sources only"
    )
    parse_fd_parser.set_defaults(func=cmd_parse_fd)

    # Committee sync
    committees_parser = subparsers.add_parser(
        "sync-committees",
        help="Fetch committee assignments from congress-legislators (free, no key)",
    )
    committees_parser.set_defaults(func=cmd_sync_committees)

    contracts_parser = subparsers.add_parser(
        "ingest-contracts",
        help="Ingest federal contract awards from USASpending (free, no key)",
    )
    contracts_parser.add_argument(
        "--start", default="2023-01-01", help="Earliest action date (default: 2023-01-01)"
    )
    contracts_parser.add_argument("--end", default=None, help="Latest action date (default: today)")
    contracts_parser.add_argument(
        "--pages-per-company",
        type=int,
        default=1,
        help=(
            "Pages of 100 award actions per traded company, largest first "
            "(default: 1). One request per company; most return nothing."
        ),
    )
    contracts_parser.set_defaults(func=cmd_ingest_contracts)

    donations_parser = subparsers.add_parser(
        "ingest-donations",
        help="Ingest corporate PAC donations from the FEC (free key required)",
    )
    donations_parser.add_argument(
        "--cycle", type=int, default=2024, help="Two-year election cycle (default: 2024)"
    )
    donations_parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Stop after this many FEC requests; rerun to resume (quota is 1,000/hour)",
    )
    donations_parser.add_argument(
        "--all-pacs",
        action="store_true",
        help="Scan every corporate PAC, not just companies members have traded (much slower)",
    )
    donations_parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-scan PACs already stored for this cycle (use after filings are amended)",
    )
    donations_parser.set_defaults(func=cmd_ingest_donations)

    significance_parser = subparsers.add_parser(
        "significance",
        help="Recompute p-values and FDR q-values over existing findings",
    )
    significance_parser.add_argument(
        "--alpha", type=float, default=None, help="False-discovery rate (default: FDR_ALPHA)"
    )
    significance_parser.add_argument(
        "--permutations",
        type=int,
        default=None,
        help="Shifted calendars per test; the p-value floor is 1/(n+1)",
    )
    significance_parser.add_argument(
        "--seed", type=int, default=None, help="Seed the permutations for a reproducible run"
    )
    significance_parser.set_defaults(func=cmd_significance)

    industries_parser = subparsers.add_parser(
        "sync-industries",
        help="Cache SEC industry codes so sector detectors see past ~70 large caps",
    )
    industries_parser.add_argument(
        "--all",
        action="store_true",
        help="Look up every SEC registrant (~8,000) rather than only traded tickers",
    )
    industries_parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Stop after this many SEC requests; rerun to continue",
    )
    industries_parser.set_defaults(func=cmd_sync_industries)

    bills_parser = subparsers.add_parser(
        "ingest-bills",
        help="Ingest bill sponsorship and committee referrals from Congress.gov (free key)",
    )
    bills_parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Stop after this many requests; rerun to continue (quota is 20,000/hour)",
    )
    bills_parser.add_argument(
        "--sponsored-only",
        action="store_true",
        help="Skip cosponsorship, which is ~20x more voluminous and produces no findings yet",
    )
    bills_parser.add_argument(
        "--skip-committees",
        action="store_true",
        help="Skip the committee-referral pass (one request per matching bill)",
    )
    bills_parser.set_defaults(func=cmd_ingest_bills)

    lobbying_parser = subparsers.add_parser(
        "ingest-lobbying",
        help="Ingest lobbying disclosures from the Senate LDA (key optional)",
    )
    lobbying_parser.add_argument(
        "--year", type=int, default=2024, help="Filing year (default: 2024)"
    )
    lobbying_parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Limit to these tickers (default: every ticker members have traded)",
    )
    lobbying_parser.set_defaults(func=cmd_ingest_lobbying)

    # Compliance command
    compliance_parser = subparsers.add_parser(
        "compliance", help="Rank members by STOCK Act filing punctuality"
    )
    compliance_parser.add_argument(
        "--min-transactions",
        type=int,
        default=5,
        help="Minimum checkable transactions to be ranked (default: 5)",
    )
    compliance_parser.add_argument(
        "-l", "--limit", type=int, default=25, help="Members to show (default: 25)"
    )
    compliance_parser.set_defaults(func=cmd_compliance)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats", help="Report detector findings with the test count behind them"
    )
    stats_parser.set_defaults(func=cmd_stats)

    # Reset command
    reset_parser = subparsers.add_parser(
        "reset",
        help="Delete ALL data and rebuild the schema (irreversible; dry run by default)",
    )
    reset_parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the reset. Without this the command only reports.",
    )
    reset_parser.add_argument(
        "--force-production",
        action="store_true",
        help="Required in addition to --yes when ENV=production",
    )
    reset_parser.add_argument(
        "--purge-pdfs",
        action="store_true",
        help="Also delete downloaded PDFs in data/disclosures/ (kept by default)",
    )
    reset_parser.set_defaults(func=cmd_reset)

    # Recount command
    recount_parser = subparsers.add_parser(
        "recount", help="Recalculate materialized member disclosure/anomaly counts"
    )
    recount_parser.set_defaults(func=cmd_recount)

    # Purge disabled anomaly types
    purge_parser = subparsers.add_parser(
        "purge-disabled",
        help="Delete persisted anomalies whose detector is now disabled",
    )
    purge_parser.add_argument(
        "--dry-run", action="store_true", help="Preview deletions without applying them"
    )
    purge_parser.set_defaults(func=cmd_purge_disabled)

    # Serve command
    serve_parser = subparsers.add_parser("serve", help="Start API server")
    serve_parser.add_argument(
        "--host", default=None, help="Host to bind to (default: $HOST or 0.0.0.0)"
    )
    serve_parser.add_argument(
        "-p", "--port", type=int, default=None, help="Port to bind to (default: $PORT or 8000)"
    )
    serve_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload for development"
    )
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
