"""
Extended Anomaly Detection for Congressional Financial Patterns

Additional detection types:
1. Trade Timing Anomalies (pre-legislation, crisis timing, earnings proximity)
2. Committee-Based Conflicts (sector overlap, defense contracts, pharma/healthcare)
3. Pattern Anomalies (perfect timing, loss avoidance, spouse trading, shell companies)
4. Wealth Source Anomalies (gifts, speaking fees, book deals, real estate flips)
5. Red Flag Combinations (multi-factor risk scoring)
"""

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.db.models import Disclosure, Member, Transaction, TransactionType

logger = logging.getLogger(__name__)

# Volume-spike tuning. These remain asserted rather than calibrated -- see D6 in
# docs/DECISIONS.md, which calls for population base rates instead.
MIN_TRADES_FOR_VOLUME_SPIKE = 8
VOLUME_SPIKE_SIGMAS = 3.0
VOLUME_SPIKE_FLAT_MULTIPLE = 5.0


def _median(values: List[float]) -> float:
    """Median of a non-empty list."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


class ExtendedAnomalyDetector:
    """Detect extended anomaly patterns in Congressional finances."""

    def __init__(self):
        self.anomalies = []

    # ========== ANOMALY 4: TRADE TIMING ANOMALIES ==========

    def detect_trade_timing_anomalies(self, db: Session) -> List[Dict]:
        """
        Detect suspicious trading timing patterns:
        - Trades within 30 days before related committee votes
        - Trades before major market announcements
        - Trades within 14 days before company earnings
        - Clustered trading (multiple members same stock same period)
        """
        from src.analysis import detector_is_disabled

        anomalies = []
        skip_perfect_timing = detector_is_disabled("perfect_timing")

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get all trades for this member (joined through Disclosure
                    # because Transaction has no direct member_id column).
                    trades = (
                        db.query(Transaction)
                        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
                        .filter(Disclosure.member_id == member.id)
                        .order_by(Transaction.transaction_date)
                        .all()
                    )

                    if not trades:
                        continue

                    # Pattern 1: Consecutive same-direction trades (unusual clustering)
                    consecutive_same_direction = self._check_consecutive_trades(trades)
                    if consecutive_same_direction:
                        anomalies.append(
                            {
                                "member_id": member.id,
                                "member_name": f"{member.first_name} {member.last_name}",
                                "chamber": member.chamber,
                                "anomaly_type": "trade_clustering",
                                "severity": "MEDIUM",
                                "title": (
                                    f"Consecutive same-direction trades "
                                    f"({consecutive_same_direction} in a row)"
                                ),
                                "pattern": "Consecutive trades in same direction within short timeframe",
                                "count": consecutive_same_direction,
                                "computed_value": Decimal(str(consecutive_same_direction)),
                                "threshold_value": Decimal("5"),
                                "description": (
                                    f"Member made {consecutive_same_direction} consecutive trades "
                                    f"in the same direction (all buys or all sells) within a short "
                                    f"period. This describes the sequence only; it does not measure "
                                    f"timing, profitability, or intent."
                                ),
                            }
                        )

                    # Pattern 2: High-volume trading before market events
                    volume_spikes = self._check_volume_spikes(trades, member)
                    if volume_spikes:
                        anomalies.extend(volume_spikes)

                    # Pattern 3: Perfect buy-low-sell-high patterns.
                    #
                    # This one cannot be skipped at the run_* level like the
                    # other two, because the loop it sits in also produces
                    # trade_clustering, volume_spikes and high_trading_frequency,
                    # which are enabled. So the check itself is skipped instead.
                    perfect_timing = (
                        None if skip_perfect_timing else self._check_perfect_timing(trades)
                    )
                    if perfect_timing:
                        anomalies.append(
                            {
                                "member_id": member.id,
                                "member_name": f"{member.first_name} {member.last_name}",
                                "chamber": member.chamber,
                                "anomaly_type": "perfect_timing",
                                "severity": "HIGH",
                                "title": (
                                    f"Suspiciously high trade success rate "
                                    f"({perfect_timing['rate']:.0f}%)"
                                ),
                                "success_rate": perfect_timing["rate"],
                                "profitable_trades": perfect_timing["count"],
                                "computed_value": Decimal(str(round(perfect_timing["rate"], 2))),
                                "threshold_value": Decimal("80"),
                                "description": (
                                    f"Member executed {perfect_timing['count']} trades with exceptional timing. "
                                    f"Success rate: {perfect_timing['rate']:.1f}%. "
                                    f"Probability of this performance by chance: <1%. "
                                    f"Timing is not evaluated against prices; see docs/DECISIONS.md."
                                ),
                            }
                        )

                except Exception as e:
                    logger.debug(f"Error analyzing timing for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in trade timing detection: {str(e)[:100]}")

        return anomalies

    def _check_consecutive_trades(self, trades: List[Transaction]) -> int:
        """Check for consecutive same-direction trades."""
        if len(trades) < 5:
            return 0

        max_consecutive = 0
        current_consecutive = 1
        prev_type = trades[0].transaction_type

        for trade in trades[1:]:
            if trade.transaction_type == prev_type:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 1
                prev_type = trade.transaction_type

        # Flag if 5+ consecutive same-direction trades
        return max_consecutive if max_consecutive >= 5 else 0

    def _check_volume_spikes(
        self,
        trades: List[Transaction],
        member: Member | None = None,
    ) -> List[Dict]:
        """Check for unusual trading volume spikes."""
        from src.analysis import transaction_amount

        anomalies = []

        amounts = [transaction_amount(t) for t in trades]
        amounts = [a for a in amounts if a > 0]

        # Mean + 3 standard deviations is not meaningful on a handful of
        # heavy-tailed points, and disclosure amounts cluster hard on a few band
        # midpoints -- a single large trade drags the mean and the deviation
        # together, so the outlier hides itself. Median absolute deviation is
        # resistant to exactly that. It still needs enough points to have a
        # stable centre, hence the higher floor.
        if len(amounts) < MIN_TRADES_FOR_VOLUME_SPIKE:
            return anomalies

        median_amount = _median(amounts)
        mad = _median([abs(a - median_amount) for a in amounts])

        if mad > 0:
            # 0.6745 rescales MAD to a normal-consistent sigma, so the cutoff
            # stays comparable to the "3 sigma" this replaced.
            threshold = median_amount + (VOLUME_SPIKE_SIGMAS * mad / 0.6745)
        else:
            # More than half the trades share one value (common when amounts
            # collapse onto the same band). Fall back to a multiple of the
            # median rather than flagging every non-median trade.
            threshold = median_amount * VOLUME_SPIKE_FLAT_MULTIPLE

        spikes = [t for t in trades if transaction_amount(t) > threshold]

        if len(spikes) >= 2:
            entry: Dict[str, Any] = {
                "anomaly_type": "volume_spikes",
                "severity": "MEDIUM",
                "title": f"Unusual trading volume spikes ({len(spikes)})",
                "spike_count": len(spikes),
                "computed_value": Decimal(str(round(threshold, 2))),
                "threshold_value": Decimal(str(round(median_amount, 2))),
                "description": (
                    f"{len(spikes)} trades were unusually large relative to this member's "
                    f"own typical trade size. Disclosures report amount bands, so sizes are "
                    f"band midpoints; this compares a member against themselves, not a "
                    f"population baseline."
                ),
            }
            if member is not None:
                entry["member_id"] = member.id
                entry["member_name"] = f"{member.first_name} {member.last_name}"
                entry["chamber"] = member.chamber
            anomalies.append(entry)

        return anomalies

    def _check_perfect_timing(self, trades: List[Transaction]) -> Dict | None:
        """Check for suspiciously good timing on trades."""
        if len(trades) < 3:
            return None

        buys = [t for t in trades if t.transaction_type == TransactionType.PURCHASE]
        sells = [t for t in trades if t.transaction_type == TransactionType.SALE]

        if not buys or not sells:
            return None

        # Simple heuristic: if all sells happen after buys (chronologically)
        # and member has sell-then-buy patterns, check for perfect execution
        profitable_patterns = 0

        for buy in buys:
            for sell in sells:
                if sell.transaction_date > buy.transaction_date:
                    # This is a profitable pattern (buy then sell)
                    profitable_patterns += 1

        success_rate = (profitable_patterns / len(buys)) * 100 if buys else 0

        # Flag if 80%+ of trades are profitable
        if success_rate >= 80 and len(buys) >= 5:
            return {
                "count": profitable_patterns,
                "rate": success_rate,
            }

        return None

    # ========== ANOMALY 5: COMMITTEE-BASED CONFLICTS ==========

    def detect_loss_avoidance(self, db: Session) -> List[Dict]:
        """
        Detect members who consistently sell before losses and hold through gains.
        Does not consult prices; disabled by default (see docs/DECISIONS.md).
        """
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    trades = (
                        db.query(Transaction)
                        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
                        .filter(Disclosure.member_id == member.id)
                        .order_by(Transaction.transaction_date)
                        .all()
                    )

                    if len(trades) < 10:
                        continue

                    # Separate by ticker to track holdings
                    holdings = defaultdict(list)
                    for trade in trades:
                        if trade.ticker:
                            holdings[trade.ticker].append(trade)

                    # Check loss avoidance pattern
                    avoidance_score = 0
                    total_patterns = 0

                    for _ticker, ticker_trades in holdings.items():
                        if len(ticker_trades) < 2:
                            continue

                        # For each buy-sell pair, check if sold before price drop
                        # (would need actual price data - this is simplified)
                        buys = [
                            t
                            for t in ticker_trades
                            if t.transaction_type == TransactionType.PURCHASE
                        ]
                        sells = [
                            t for t in ticker_trades if t.transaction_type == TransactionType.SALE
                        ]

                        if buys and sells:
                            # Check if sells always follow buys (not holding through drops)
                            for sell in sells:
                                for buy in buys:
                                    if buy.transaction_date < sell.transaction_date:
                                        avoidance_score += 1
                                        total_patterns += 1

                    # Flag if pattern is strong
                    if total_patterns > 5 and (avoidance_score / total_patterns) > 0.8:
                        rate = (avoidance_score / total_patterns) * 100
                        anomalies.append(
                            {
                                "member_id": member.id,
                                "member_name": f"{member.first_name} {member.last_name}",
                                "chamber": member.chamber,
                                "anomaly_type": "loss_avoidance",
                                "severity": "HIGH",
                                "title": f"Loss-avoidance pattern ({rate:.0f}% rate)",
                                "avoidance_rate": rate,
                                "pattern_count": total_patterns,
                                "computed_value": Decimal(str(round(rate, 2))),
                                "threshold_value": Decimal("80"),
                                "description": (
                                    f"Member demonstrates loss-avoidance pattern in {total_patterns} trading instances. "
                                    f"Success rate: {rate:.1f}%. "
                                    f"No price data is consulted; see docs/DECISIONS.md."
                                ),
                            }
                        )

                except Exception as e:
                    logger.debug(
                        f"Error checking loss avoidance for {member.first_name}: {str(e)[:50]}"
                    )

        except Exception as e:
            logger.error(f"Error in loss avoidance detection: {str(e)[:100]}")

        return anomalies

    # ========== ANOMALY 7: RED FLAG COMBINATIONS ==========

    def detect_red_flag_combinations(self, db: Session, previous_results: Dict) -> List[Dict]:
        """
        Detect high-risk combinations of multiple anomalies.
        Multi-factor risk scoring.
        """
        anomalies = []

        # Red flag combinations from advanced detector
        wealth_anomalies = previous_results.get("wealth_anomalies", [])
        asset_anomalies = previous_results.get("asset_anomalies", [])
        stock_anomalies = previous_results.get("stock_anomalies", [])
        timing_anomalies = previous_results.get("timing_anomalies", [])
        conflict_anomalies = previous_results.get("conflict_anomalies", [])

        # Build member anomaly map.
        #
        # The disabled types have to be filtered HERE, not only where anomalies
        # are written. `outperforming_trades`, `perfect_timing` and
        # `loss_avoidance` are disabled because their arithmetic is indefensible
        # -- a hardcoded 10% benchmark, a rate that exceeds 100%, a ratio that is
        # always exactly 100% -- and the persist-time gate stops those rows being
        # stored. It does not stop them being COUNTED here. So a member could be
        # published as "multi-factor risk" on the strength of three findings that
        # this project has already judged unfit to show, with the disabled
        # detectors named in the description of a row that is served.
        #
        # Re-deriving the risk from findings that were themselves suppressed is
        # the one thing a multi-factor detector must not do.
        from src.config import get_settings

        disabled = get_settings().disabled_anomaly_types_set

        member_anomaly_map = defaultdict(list)

        for anomaly in (
            wealth_anomalies
            + asset_anomalies
            + stock_anomalies
            + timing_anomalies
            + conflict_anomalies
        ):
            member_id = anomaly.get("member_id")
            if member_id and anomaly.get("anomaly_type") not in disabled:
                member_anomaly_map[member_id].append(anomaly)

        # Flag members with multiple anomalies
        for member_id, anomalies_list in member_anomaly_map.items():
            # Distinct TYPES, which is what the title has always claimed. This
            # counted findings, so three `rapid_asset_appreciation` rows for one
            # member were published as "3 different anomaly types" -- a
            # single-signal member described as a multi-signal one, which is the
            # entire content of this detector.
            distinct_types = {
                a.get("anomaly_type") for a in anomalies_list if a.get("anomaly_type")
            }
            if len(distinct_types) >= 3:
                severity_scores = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}

                total_score = sum(
                    severity_scores.get(a.get("severity", "LOW"), 0) for a in anomalies_list
                )

                overall_severity = "CRITICAL" if total_score >= 6 else "HIGH"

                # Get member name from first anomaly
                member_name = anomalies_list[0].get("member_name", "Unknown")

                anomalies.append(
                    {
                        "member_id": member_id,
                        "member_name": member_name,
                        "anomaly_type": "multi_factor_risk",
                        "severity": overall_severity,
                        "title": (
                            f"Multi-factor risk: {len(distinct_types)} different anomaly types"
                        ),
                        "anomaly_count": len(anomalies_list),
                        "risk_score": total_score,
                        "anomaly_types": [a.get("anomaly_type") for a in anomalies_list],
                        "computed_value": Decimal(str(total_score)),
                        "threshold_value": Decimal("3"),
                        "description": (
                            f"Member matched {len(anomalies_list)} different detectors "
                            f"(combined score: {total_score}/10). "
                            f"Detectors: {', '.join(sorted(set(a.get('anomaly_type', 'unknown') for a in anomalies_list)))}. "
                            f"This counts how many patterns matched; it does not weight them by "
                            f"confidence and applies no correction for running many detectors "
                            f"across many members, so some overlap is expected by chance."
                        ),
                    }
                )

        return anomalies


def run_extended_anomaly_detection(
    db: Session,
    previous_results: Dict | None = None,
    persist: bool = True,
) -> Dict:
    """Run all extended anomaly detection types.

    When `persist` is true, detected anomalies are written to the database.
    """
    from src.analysis import detector_is_disabled, persist_anomalies

    detector = ExtendedAnomalyDetector()

    logger.info("\n" + "=" * 70)
    logger.info("EXTENDED ANOMALY DETECTION")
    logger.info("=" * 70 + "\n")

    logger.info("1. Detecting trade timing anomalies...")
    timing_anomalies = detector.detect_trade_timing_anomalies(db)
    logger.info(f"   Found {len(timing_anomalies)} anomalies\n")

    # Committee conflicts moved to src/analysis/committee_conflicts.py, which
    # joins real assignments from congress-legislators instead of guessing from
    # ticker substrings. Kept as an empty list so the result shape is unchanged
    # for callers reading combined_results.
    conflict_anomalies: List[Dict] = []

    # Same reasoning as stock outperformance: `loss_avoidance` increments its
    # numerator and denominator on the same branch, so its rate is always
    # exactly 100%. It cost 17m17s to produce 18 findings that were discarded.
    if detector_is_disabled("loss_avoidance"):
        logger.info("3. Loss avoidance is disabled; not running it.\n")
        loss_anomalies: List[Dict] = []
    else:
        logger.info("3. Detecting loss avoidance patterns...")
        loss_anomalies = detector.detect_loss_avoidance(db)
        logger.info(f"   Found {len(loss_anomalies)} anomalies\n")

    combined_results = previous_results or {
        "wealth_anomalies": [],
        "asset_anomalies": [],
        "stock_anomalies": [],
    }
    combined_results.update(
        {
            "timing_anomalies": timing_anomalies,
            "conflict_anomalies": conflict_anomalies,
            "loss_avoidance_anomalies": loss_anomalies,
        }
    )

    logger.info("4. Detecting multi-factor risk combinations...")
    combination_anomalies = detector.detect_red_flag_combinations(db, combined_results)
    logger.info(f"   Found {len(combination_anomalies)} anomalies\n")

    persisted = 0
    if persist:
        persisted += persist_anomalies(db, timing_anomalies)
        persisted += persist_anomalies(db, conflict_anomalies)
        persisted += persist_anomalies(db, loss_anomalies)
        persisted += persist_anomalies(db, combination_anomalies)
        logger.info(f"Persisted {persisted} new anomalies to database.\n")

    total = (
        len(timing_anomalies)
        + len(conflict_anomalies)
        + len(loss_anomalies)
        + len(combination_anomalies)
    )
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Extended anomalies detected: {total}")
    logger.info(f"  • Trade Timing: {len(timing_anomalies)}")
    logger.info(f"  • Committee Conflicts: {len(conflict_anomalies)}")
    logger.info(f"  • Loss Avoidance: {len(loss_anomalies)}")
    logger.info(f"  • Multi-Factor Risk: {len(combination_anomalies)}")
    logger.info("=" * 70 + "\n")

    return {
        "timing_anomalies": timing_anomalies,
        "conflict_anomalies": conflict_anomalies,
        "loss_avoidance_anomalies": loss_anomalies,
        "combination_anomalies": combination_anomalies,
        "persisted": persisted,
        "total": total,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    from src.db.database import SessionLocal

    db = SessionLocal()
    results = run_extended_anomaly_detection(db)
    db.close()
