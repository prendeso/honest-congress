#!/usr/bin/env python
"""
Complete Anomaly Detection Orchestrator

Runs all 7 anomaly detection types:
1. Net worth vs salary
2. Asset appreciation
3. Stock performance vs benchmarks
4. Trade timing anomalies
5. Committee-based conflicts
6. Loss avoidance patterns
7. Multi-factor risk combinations
"""
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_complete_anomaly_detection():
    """Run all anomaly detection in sequence."""

    print("\n" + "="*80)
    print("  COMPLETE CONGRESSIONAL ANOMALY DETECTION SYSTEM")
    print("  7 Detection Types + Risk Scoring + Multi-Factor Analysis")
    print("="*80 + "\n")

    try:
        from src.db.database import SessionLocal
        from src.analysis.advanced_anomaly_detector import run_advanced_anomaly_detection
        from src.analysis.extended_anomaly_detector import run_extended_anomaly_detection

        db = SessionLocal()

        logger.info("Starting complete anomaly detection pipeline...\n")
        start_time = time.time()

        # Phase 1: Advanced Anomaly Detection
        logger.info("="*80)
        logger.info("PHASE 1: ADVANCED ANOMALY DETECTION (3 types)")
        logger.info("="*80 + "\n")

        phase1_start = time.time()
        advanced_results = run_advanced_anomaly_detection(db)
        phase1_time = time.time() - phase1_start

        logger.info(f"Phase 1 completed in {phase1_time:.2f}s\n")

        # Phase 2: Extended Anomaly Detection
        logger.info("="*80)
        logger.info("PHASE 2: EXTENDED ANOMALY DETECTION (4 types)")
        logger.info("="*80 + "\n")

        phase2_start = time.time()
        extended_results = run_extended_anomaly_detection(db, advanced_results)
        phase2_time = time.time() - phase2_start

        logger.info(f"Phase 2 completed in {phase2_time:.2f}s\n")

        # Combined Summary
        total_time = time.time() - start_time

        logger.info("="*80)
        logger.info("COMPLETE RESULTS SUMMARY")
        logger.info("="*80 + "\n")

        # Count all anomalies
        advanced_total = advanced_results.get("total", 0)
        extended_total = extended_results.get("total", 0)
        total_anomalies = advanced_total + extended_total

        logger.info("Advanced Anomalies (3 types):")
        logger.info(f"  • Wealth vs Salary: {len(advanced_results.get('wealth_anomalies', []))}")
        logger.info(f"  • Asset Appreciation: {len(advanced_results.get('asset_anomalies', []))}")
        logger.info(f"  • Stock Outperformance: {len(advanced_results.get('stock_anomalies', []))}")
        logger.info(f"  Total: {advanced_total}\n")

        logger.info("Extended Anomalies (4 types):")
        logger.info(f"  • Trade Timing: {len(extended_results.get('timing_anomalies', []))}")
        logger.info(f"  • Committee Conflicts: {len(extended_results.get('conflict_anomalies', []))}")
        logger.info(f"  • Loss Avoidance: {len(extended_results.get('loss_avoidance_anomalies', []))}")
        logger.info(f"  • Multi-Factor Risk: {len(extended_results.get('combination_anomalies', []))}")
        logger.info(f"  Total: {extended_total}\n")

        logger.info(f"GRAND TOTAL: {total_anomalies} anomalies detected\n")

        logger.info("Timing Breakdown:")
        logger.info(f"  • Phase 1 (Advanced): {phase1_time:.2f}s")
        logger.info(f"  • Phase 2 (Extended): {phase2_time:.2f}s")
        logger.info(f"  • Total: {total_time:.2f}s\n")

        logger.info("="*80)
        logger.info("CRITICAL FINDINGS")
        logger.info("="*80 + "\n")

        # Count CRITICAL severity
        critical_count = 0
        high_count = 0

        for anomaly_list in [
            advanced_results.get("wealth_anomalies", []),
            advanced_results.get("asset_anomalies", []),
            advanced_results.get("stock_anomalies", []),
            extended_results.get("timing_anomalies", []),
            extended_results.get("conflict_anomalies", []),
            extended_results.get("loss_avoidance_anomalies", []),
            extended_results.get("combination_anomalies", []),
        ]:
            for anomaly in anomaly_list:
                if anomaly.get("severity") == "CRITICAL":
                    critical_count += 1
                elif anomaly.get("severity") == "HIGH":
                    high_count += 1

        logger.info(f"CRITICAL Severity: {critical_count}")
        logger.info(f"HIGH Severity: {high_count}")
        logger.info(f"Other Severity: {total_anomalies - critical_count - high_count}\n")

        # Show top findings by category
        logger.info("="*80)
        logger.info("TOP FINDINGS BY CATEGORY")
        logger.info("="*80 + "\n")

        categories = [
            ("Wealth vs Salary", advanced_results.get("wealth_anomalies", [])),
            ("Asset Appreciation", advanced_results.get("asset_anomalies", [])),
            ("Stock Outperformance", advanced_results.get("stock_anomalies", [])),
            ("Trade Timing", extended_results.get("timing_anomalies", [])),
            ("Committee Conflicts", extended_results.get("conflict_anomalies", [])),
            ("Loss Avoidance", extended_results.get("loss_avoidance_anomalies", [])),
            ("Multi-Factor Risk", extended_results.get("combination_anomalies", [])),
        ]

        for category_name, anomalies in categories:
            if anomalies:
                logger.info(f"{category_name}: {len(anomalies)} found")

                # Show top 3 by severity
                critical = [a for a in anomalies if a.get("severity") == "CRITICAL"]
                if critical:
                    logger.info(f"  • CRITICAL: {min(3, len(critical))} members")
                    for anomaly in critical[:3]:
                        logger.info(f"    - {anomaly.get('member_name', 'Unknown')}")

        logger.info("\n" + "="*80)
        logger.info("NEXT STEPS")
        logger.info("="*80 + "\n")

        logger.info("1. Review CRITICAL severity findings:")
        logger.info("   python -m src.cli report --severity CRITICAL\n")

        logger.info("2. Generate full report:")
        logger.info("   python -m src.cli report --all\n")

        logger.info("3. View in dashboard:")
        logger.info("   python -m src.cli serve --port 8001\n")

        logger.info("4. Export to CSV:")
        logger.info("   python -m src.cli export --format csv\n")

        logger.info("="*80)
        logger.info(f"DETECTION COMPLETE - {total_anomalies} anomalies ready for investigation")
        logger.info("="*80 + "\n")

        db.close()

        return {
            "advanced": advanced_results,
            "extended": extended_results,
            "total": total_anomalies,
            "critical": critical_count,
            "high": high_count,
            "time": total_time,
        }

    except Exception as e:
        logger.error(f"Error in complete detection pipeline: {str(e)}", exc_info=True)
        return None


if __name__ == "__main__":
    results = run_complete_anomaly_detection()

    if results:
        print("\n[OK] Detection Complete!")
        print(f"Total anomalies: {results['total']}")
        print(f"CRITICAL: {results['critical']}")
        print(f"HIGH: {results['high']}")
        print(f"Time: {results['time']:.2f}s")
    else:
        print("\n[ERROR] Detection failed - check logs above")

