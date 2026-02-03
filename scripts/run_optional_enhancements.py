#!/usr/bin/env python
"""
Optional Enhancement Orchestrator
Runs all three optional enhancements:
1. FD Asset Parsing
2. FD Income Extraction
3. Committee Conflict Mapping
"""
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_enhancements():
    """Run all optional enhancements."""

    print("\n" + "="*70)
    print("  OPTIONAL ENHANCEMENTS - RUNNING ALL THREE")
    print("="*70 + "\n")

    logger.info("Starting optional enhancement phase...")
    logger.info(f"{'='*70}\n")

    start_time = time.time()

    # Enhancement 1: Asset Parsing
    logger.info("ENHANCEMENT 1/3: FD Asset Value Parsing")
    logger.info(f"{'─'*70}")
    try:
        from src.parsing.fd_asset_parser import FDAssetParser

        parser = FDAssetParser()
        parser.parse_all_disclosures()

        logger.info("✓ Asset parsing complete")
        asset_time = time.time() - start_time
        logger.info(f"  Time: {asset_time:.1f}s\n")
    except Exception as e:
        logger.error(f"✗ Asset parsing failed: {str(e)[:100]}\n")

    # Enhancement 2: Income Parsing
    logger.info("ENHANCEMENT 2/3: FD Income Source Extraction")
    logger.info(f"{'─'*70}")
    try:
        from src.parsing.fd_income_parser import FDIncomeParser

        parser = FDIncomeParser()
        parser.parse_all_disclosures()

        logger.info("✓ Income parsing complete")
        income_time = time.time() - start_time - asset_time
        logger.info(f"  Time: {income_time:.1f}s\n")
    except Exception as e:
        logger.error(f"✗ Income parsing failed: {str(e)[:100]}\n")

    # Enhancement 3: Committee Mapping
    logger.info("ENHANCEMENT 3/3: Committee Conflict Mapping")
    logger.info(f"{'─'*70}")
    try:
        from src.parsing.committee_conflict_mapper import CommitteeConflictMapper

        mapper = CommitteeConflictMapper()
        mapper.map_committees()

        logger.info("✓ Committee mapping complete")
        committee_time = time.time() - start_time - asset_time - income_time
        logger.info(f"  Time: {committee_time:.1f}s\n")
    except Exception as e:
        logger.error(f"✗ Committee mapping failed: {str(e)[:100]}\n")

    total_time = time.time() - start_time

    # Summary
    logger.info(f"{'='*70}")
    logger.info("ENHANCEMENT SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"✓ All optional enhancements completed!")
    logger.info(f"\nTime breakdown:")
    logger.info(f"  1. Asset parsing:        {asset_time:.1f}s")
    logger.info(f"  2. Income extraction:    {income_time:.1f}s")
    logger.info(f"  3. Committee mapping:    {committee_time:.1f}s")
    logger.info(f"  ─────────────────────────")
    logger.info(f"  Total:                   {total_time:.1f}s")
    logger.info(f"\n{'='*70}\n")

    logger.info("Next steps:")
    logger.info("  1. Run anomaly detection with enhanced data:")
    logger.info("     python -m src.cli analyze")
    logger.info("  2. Start dashboard:")
    logger.info("     python -m src.cli serve --port 8001")
    print()
    logger.info("Your system now has:")
    logger.info("  ✓ Asset value extraction from FD")
    logger.info("  ✓ Income source analysis")
    logger.info("  ✓ Committee-sector conflict mapping")
    logger.info("  ✓ Ready for Phase 3 anomaly detection!")
    print()


if __name__ == "__main__":
    run_enhancements()


