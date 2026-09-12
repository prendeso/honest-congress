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


def cmd_ingest_trades(args):
    """Ingest congressional trades from QuiverQuant.

    Skips cleanly when no API key is configured rather than raising, so the
    scheduled workflow can call this unconditionally -- and so it becomes a
    no-op the moment the key is removed.
    """
    from src.config import get_settings
    from src.ingestion.quiverquant import ingest_quiverquant_trades

    if not get_settings().quiverquant_api_key:
        print("QUIVERQUANT_API_KEY is not set; skipping trade ingestion.")
        return

    print("Importing congressional trades from QuiverQuant...")

    with get_db() as db:
        result = ingest_quiverquant_trades(db, chamber=args.chamber)

    with get_db() as db:
        recalculate_member_counts(db)

    print("\nTrade Ingestion Complete:")
    print(f"  Imported: {result['imported']}")
    print(f"  Duplicates: {result['duplicates']}")
    print(f"  Errors: {result['errors']}")
    print(f"  Total processed: {result['imported'] + result['duplicates'] + result['errors']}")


def cmd_analyze(args):
    """Run anomaly analysis."""
    from src.analysis import (
        analyze_trades,
        run_advanced_anomaly_detection,
        run_extended_anomaly_detection,
    )

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

        print("\nExtended Analysis Results:")
        print(f"  Trade Timing: {len(extended.get('timing_anomalies', []))}")
        print(f"  Committee Conflicts: {len(extended.get('conflict_anomalies', []))}")
        print(f"  Loss Avoidance: {len(extended.get('loss_avoidance_anomalies', []))}")
        print(f"  Multi-Factor Risk: {len(extended.get('combination_anomalies', []))}")

        total_anomalies += advanced.get("total", 0) + extended.get("total", 0)

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
        f"\nContext: ~{summary['approximate_tests_run']} detector-member tests produced "
        f"{summary['total_findings']} findings across {summary['members']} members."
    )


def cmd_performance(args):
    """Analyze trading performance vs benchmarks."""
    from datetime import datetime

    from src.analysis.performance_analyzer import PerformanceAnalyzer

    analyzer = PerformanceAnalyzer()

    start_date = datetime.fromisoformat(args.start_date) if args.start_date else None
    end_date = datetime.fromisoformat(args.end_date) if args.end_date else None

    with get_db() as db:
        if args.member_id:
            print(f"Analyzing performance for member {args.member_id}...")
            result = analyzer.compare_to_benchmarks(db, args.member_id, start_date, end_date)

            member = result.get("member", {})
            perf = result.get("member_performance", {})

            print(
                f"\n{member.get('name', 'Unknown')} ({member.get('party', '')} - {member.get('state', '')})"
            )
            print(
                f"  Period: {result.get('period', {}).get('start', '')} to {result.get('period', {}).get('end', '')}"
            )
            print(f"  Trades analyzed: {perf.get('trades_analyzed', 0)}")
            print(f"  Total invested: ${perf.get('total_invested', 0):,.0f}")
            print(
                f"  Estimated return: {perf.get('estimated_return_pct', 'N/A'):.1f}%"
                if perf.get("estimated_return_pct")
                else "  Estimated return: N/A"
            )

            print("\nBenchmark Comparison:")
            for name, data in result.get("benchmarks", {}).items():
                ret = data.get("return_pct")
                print(f"  {name.upper()}: {ret:.1f}%" if ret else f"  {name.upper()}: N/A")

            alpha = result.get("alpha_vs_sp500")
            if alpha is not None:
                print(f"\n  Alpha vs S&P 500: {alpha:+.1f}%")
                print(f"  Beats S&P 500: {'Yes' if result.get('beats_sp500') else 'No'}")
                print(f"  Beats Buffett: {'Yes' if result.get('beats_buffett') else 'No'}")

        elif args.rankings:
            print("Ranking members by trading performance...")
            result = analyzer.rank_members_by_performance(
                db, start_date, end_date, min_trades=args.min_trades, limit=args.limit
            )

            print(
                f"\nPeriod: {result.get('period', {}).get('start', '')} to {result.get('period', {}).get('end', '')}"
            )
            print(f"Members analyzed: {result.get('total_members_analyzed', 0)}")
            print(f"Members beating S&P 500: {result.get('members_beating_sp500', 0)}")

            benchmarks = result.get("benchmarks", {})
            print(
                f"\nBenchmarks: S&P 500: {benchmarks.get('sp500', 'N/A'):.1f}%, Buffett: {benchmarks.get('buffett', 'N/A'):.1f}%"
            )

            print(f"\nTop {args.limit} Performers:")
            for i, p in enumerate(result.get("top_performers", [])[: args.limit], 1):
                member = p.get("member", {})
                ret = p.get("member_performance", {}).get("estimated_return_pct", 0)
                alpha = p.get("alpha_vs_sp500", 0)
                print(
                    f"  {i}. {member.get('name', 'Unknown')} ({member.get('party', '')}-{member.get('state', '')}): {ret:.1f}% (α: {alpha:+.1f}%)"
                )

        else:
            print("Getting performance summary...")
            result = analyzer.get_performance_summary(db)

            print("\nPerformance Summary:")
            print(f"  Members with transactions: {result.get('members_with_transactions', 0)}")
            print(f"  Total transactions: {result.get('total_transactions', 0)}")
            print(f"  Unique tickers: {result.get('unique_tickers', 0)}")

            if result.get("top_traded_tickers"):
                print("\n  Top traded tickers:")
                for t in result["top_traded_tickers"][:5]:
                    print(f"    {t['ticker']}: {t['count']} trades")


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
            delay=args.delay,
        )

    print("\nParsing complete:")
    print(f"  Successfully parsed: {result['parsed']}")
    print(f"  Failed: {result['failed']}")
    print(f"  Skipped: {result['skipped']}")


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
    """Fix incorrect disclosure URLs in the database."""
    from src.db.models import Disclosure

    BASE_URL = "https://disclosures-clerk.house.gov/public_disc"

    def get_correct_url(d):
        if d.document_id.startswith("QANT_"):
            return d.document_url or ""
        if d.is_ptr:
            return f"{BASE_URL}/ptr-pdfs/{d.filing_year}/{d.document_id}.pdf"
        return f"{BASE_URL}/financial-pdfs/{d.filing_year}/{d.document_id}.pdf"

    print("Checking disclosure URLs...")

    with get_db() as db:
        disclosures = db.query(Disclosure).filter(~Disclosure.document_id.like("QANT_%")).all()

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


def cmd_stats(args):
    """Show detector output in context: how many tests, how many findings."""
    import json

    with get_db() as db:
        summary = detection_summary(db)

    print(json.dumps(summary, indent=2))


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

    # Ingest trades command
    ingest_trades_parser = subparsers.add_parser(
        "ingest-trades", help="Import trades from QuiverQuant"
    )
    ingest_trades_parser.add_argument(
        "-c",
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Which chamber to import (default: both)",
    )
    ingest_trades_parser.set_defaults(func=cmd_ingest_trades)

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
    perf_parser = subparsers.add_parser(
        "performance", help="Analyze trading performance vs benchmarks"
    )
    perf_parser.add_argument("-m", "--member-id", type=int, help="Analyze specific member by ID")
    perf_parser.add_argument(
        "-r", "--rankings", action="store_true", help="Show ranked list of performers"
    )
    perf_parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    perf_parser.add_argument("--end-date", type=str, help="End date (YYYY-MM-DD)")
    perf_parser.add_argument(
        "--min-trades", type=int, default=5, help="Minimum trades for rankings (default: 5)"
    )
    perf_parser.add_argument(
        "-l", "--limit", type=int, default=10, help="Number of top performers to show (default: 10)"
    )
    perf_parser.set_defaults(func=cmd_performance)

    # Parse FD assets / income
    parse_fd_parser = subparsers.add_parser(
        "parse-fd", help="Parse assets and income sources from annual FD filings"
    )
    parse_fd_parser.add_argument("--assets-only", action="store_true", help="Parse assets only")
    parse_fd_parser.add_argument(
        "--income-only", action="store_true", help="Parse income sources only"
    )
    parse_fd_parser.set_defaults(func=cmd_parse_fd)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats", help="Report detector findings with the test count behind them"
    )
    stats_parser.set_defaults(func=cmd_stats)

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
