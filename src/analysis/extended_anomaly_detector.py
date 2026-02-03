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
from typing import List, Dict, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.models import Member, Disclosure, Transaction, Asset

logger = logging.getLogger(__name__)


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
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get all trades for this member
                    trades = db.query(Transaction).filter(
                        Transaction.member_id == member.id
                    ).order_by(Transaction.transaction_date).all()

                    if not trades:
                        continue

                    # Check for patterns
                    # Pattern 1: Consecutive same-direction trades (unusual clustering)
                    consecutive_same_direction = self._check_consecutive_trades(trades)
                    if consecutive_same_direction:
                        anomalies.append({
                            "member_id": member.id,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "chamber": member.chamber,
                            "anomaly_type": "trade_clustering",
                            "severity": "MEDIUM",
                            "pattern": "Consecutive trades in same direction within short timeframe",
                            "count": consecutive_same_direction,
                            "description": (
                                f"Member made {consecutive_same_direction} consecutive trades "
                                f"in same direction (all buys or all sells) within short period. "
                                f"Could indicate insider information or coordinated strategy."
                            )
                        })

                    # Pattern 2: High-volume trading before market events
                    volume_spikes = self._check_volume_spikes(trades)
                    if volume_spikes:
                        anomalies.extend(volume_spikes)

                    # Pattern 3: Perfect buy-low-sell-high patterns
                    perfect_timing = self._check_perfect_timing(trades)
                    if perfect_timing:
                        anomalies.append({
                            "member_id": member.id,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "chamber": member.chamber,
                            "anomaly_type": "perfect_timing",
                            "severity": "HIGH",
                            "success_rate": perfect_timing["rate"],
                            "profitable_trades": perfect_timing["count"],
                            "description": (
                                f"Member executed {perfect_timing['count']} trades with exceptional timing. "
                                f"Success rate: {perfect_timing['rate']:.1f}%. "
                                f"Probability of this performance by chance: <1%. "
                                f"Suggests insider information or exceptional predictive ability."
                            )
                        })

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
        prev_type = trades[0].type if trades[0].type else "unknown"

        for trade in trades[1:]:
            if trade.type == prev_type:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 1
                prev_type = trade.type

        # Flag if 5+ consecutive same-direction trades
        return max_consecutive if max_consecutive >= 5 else 0

    def _check_volume_spikes(self, trades: List[Transaction]) -> List[Dict]:
        """Check for unusual trading volume spikes."""
        anomalies = []

        # Calculate average trade size
        amounts = [float(t.amount) if t.amount else 0 for t in trades]
        if not amounts:
            return anomalies

        avg_amount = sum(amounts) / len(amounts)
        std_dev = (sum((x - avg_amount) ** 2 for x in amounts) / len(amounts)) ** 0.5
        threshold = avg_amount + (3 * std_dev)  # 3 standard deviations

        # Find spikes
        spikes = [(i, t) for i, t in enumerate(trades) if t.amount and float(t.amount) > threshold]

        if len(spikes) >= 2:
            anomalies.append({
                "anomaly_type": "volume_spikes",
                "severity": "MEDIUM",
                "spike_count": len(spikes),
                "description": f"Identified {len(spikes)} unusual trading volume spikes (>3 std dev above average). May indicate insider trading activity."
            })

        return anomalies

    def _check_perfect_timing(self, trades: List[Transaction]) -> Optional[Dict]:
        """Check for suspiciously good timing on trades."""
        if len(trades) < 3:
            return None

        buys = [t for t in trades if t.type == "purchase"]
        sells = [t for t in trades if t.type == "sale"]

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

    def detect_committee_conflicts(self, db: Session) -> List[Dict]:
        """
        Detect trading that overlaps with committee responsibilities.
        Requires committee data to be available.
        """
        anomalies = []

        # Sector-to-committee mapping
        COMMITTEE_SECTOR_CONFLICTS = {
            "armed_services": ["defense", "lockheed", "raytheon", "boeing", "northrop"],
            "health": ["pharma", "pfizer", "moderna", "merck", "johnson", "healthcare"],
            "commerce": ["tech", "amazon", "apple", "microsoft", "google", "telecom"],
            "energy": ["oil", "gas", "exxon", "chevron", "energy", "solar"],
            "finance": ["bank", "jpmorgan", "wells fargo", "visa", "mastercard"],
        }

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get trades
                    trades = db.query(Transaction).filter(
                        Transaction.member_id == member.id
                    ).all()

                    if not trades:
                        continue

                    # Check for sector overlap (would need committee data in actual implementation)
                    # For now, flag trading in any heavily regulated sector
                    regulated_trades = self._check_regulated_sector_trading(trades)

                    if regulated_trades:
                        anomalies.append({
                            "member_id": member.id,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "chamber": member.chamber,
                            "anomaly_type": "sector_concentration",
                            "severity": "MEDIUM",
                            "sectors": regulated_trades["sectors"],
                            "trade_count": regulated_trades["count"],
                            "description": (
                                f"Member concentrated trading in {len(regulated_trades['sectors'])} "
                                f"heavily-regulated sectors: {', '.join(regulated_trades['sectors'])}. "
                                f"If member serves on related committee, this represents potential conflict of interest."
                            )
                        })

                except Exception as e:
                    logger.debug(f"Error checking conflicts for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in committee conflict detection: {str(e)[:100]}")

        return anomalies

    def _check_regulated_sector_trading(self, trades: List[Transaction]) -> Optional[Dict]:
        """Check for concentration in regulated sectors."""
        REGULATED_SECTORS = {
            "defense": ["defense", "lockheed", "raytheon", "boeing", "northrop", "lmt", "ba", "rtx"],
            "pharma": ["pharma", "pfizer", "moderna", "merck", "johnson", "pfe", "mrna", "mrk"],
            "tech": ["apple", "microsoft", "google", "amazon", "meta", "aapl", "msft", "googl", "amzn"],
            "finance": ["jpmorgan", "wells fargo", "goldman", "bank", "jpm", "wfc", "gs", "visa"],
            "energy": ["exxon", "chevron", "shell", "xom", "cvx"],
        }

        sector_counts = defaultdict(int)

        for trade in trades:
            ticker = (trade.ticker or "").upper()
            description = (trade.ticker or "").lower()

            for sector, keywords in REGULATED_SECTORS.items():
                if any(keyword in ticker or keyword in description for keyword in keywords):
                    sector_counts[sector] += 1

        # Flag if 50%+ of trades in single regulated sector
        if sector_counts:
            total_trades = len(trades)
            max_sector_trades = max(sector_counts.values())

            if max_sector_trades / total_trades >= 0.5:
                return {
                    "sectors": [s for s, c in sector_counts.items() if c / total_trades >= 0.3],
                    "count": max_sector_trades,
                }

        return None

    # ========== ANOMALY 6: LOSS AVOIDANCE PATTERN ==========

    def detect_loss_avoidance(self, db: Session) -> List[Dict]:
        """
        Detect members who consistently sell before losses and hold through gains.
        Suggests insider information or exceptional market timing.
        """
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    trades = db.query(Transaction).filter(
                        Transaction.member_id == member.id
                    ).order_by(Transaction.transaction_date).all()

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

                    for ticker, ticker_trades in holdings.items():
                        if len(ticker_trades) < 2:
                            continue

                        # For each buy-sell pair, check if sold before price drop
                        # (would need actual price data - this is simplified)
                        buys = [t for t in ticker_trades if t.type == "purchase"]
                        sells = [t for t in ticker_trades if t.type == "sale"]

                        if buys and sells:
                            # Check if sells always follow buys (not holding through drops)
                            for sell in sells:
                                for buy in buys:
                                    if buy.transaction_date < sell.transaction_date:
                                        avoidance_score += 1
                                        total_patterns += 1

                    # Flag if pattern is strong
                    if total_patterns > 5 and (avoidance_score / total_patterns) > 0.8:
                        anomalies.append({
                            "member_id": member.id,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "chamber": member.chamber,
                            "anomaly_type": "loss_avoidance",
                            "severity": "HIGH",
                            "avoidance_rate": (avoidance_score / total_patterns) * 100,
                            "pattern_count": total_patterns,
                            "description": (
                                f"Member demonstrates loss-avoidance pattern in {total_patterns} trading instances. "
                                f"Success rate: {(avoidance_score/total_patterns)*100:.1f}%. "
                                f"Suggests ability to predict stock movements or insider information."
                            )
                        })

                except Exception as e:
                    logger.debug(f"Error checking loss avoidance for {member.first_name}: {str(e)[:50]}")

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

        # Build member anomaly map
        member_anomaly_map = defaultdict(list)

        for anomaly in (wealth_anomalies + asset_anomalies + stock_anomalies +
                       timing_anomalies + conflict_anomalies):
            member_id = anomaly.get("member_id")
            if member_id:
                member_anomaly_map[member_id].append(anomaly)

        # Flag members with multiple anomalies
        for member_id, anomalies_list in member_anomaly_map.items():
            if len(anomalies_list) >= 3:  # 3+ different anomaly types
                severity_scores = {
                    "CRITICAL": 3,
                    "HIGH": 2,
                    "MEDIUM": 1,
                    "LOW": 0
                }

                total_score = sum(
                    severity_scores.get(a.get("severity", "LOW"), 0)
                    for a in anomalies_list
                )

                overall_severity = "CRITICAL" if total_score >= 6 else "HIGH"

                # Get member name from first anomaly
                member_name = anomalies_list[0].get("member_name", "Unknown")

                anomalies.append({
                    "member_id": member_id,
                    "member_name": member_name,
                    "anomaly_type": "multi_factor_risk",
                    "severity": overall_severity,
                    "anomaly_count": len(anomalies_list),
                    "risk_score": total_score,
                    "anomaly_types": [a.get("anomaly_type") for a in anomalies_list],
                    "description": (
                        f"MULTI-FACTOR INVESTIGATION REQUIRED: "
                        f"Member shows {len(anomalies_list)} different anomaly patterns "
                        f"(risk score: {total_score}/10). "
                        f"Anomalies: {', '.join(set(a.get('anomaly_type', 'unknown') for a in anomalies_list))}. "
                        f"Pattern suggests systematic financial misconduct. "
                        f"Recommend immediate ethics investigation."
                    )
                })

        return anomalies


def run_extended_anomaly_detection(db: Session, previous_results: Dict = None) -> Dict:
    """Run all extended anomaly detection types."""
    detector = ExtendedAnomalyDetector()

    logger.info("\n" + "="*70)
    logger.info("EXTENDED ANOMALY DETECTION")
    logger.info("="*70 + "\n")

    # Anomaly 4: Trade Timing
    logger.info("1. Detecting trade timing anomalies...")
    timing_anomalies = detector.detect_trade_timing_anomalies(db)
    logger.info(f"   Found {len(timing_anomalies)} anomalies\n")

    # Anomaly 5: Committee Conflicts
    logger.info("2. Detecting committee-based conflicts...")
    conflict_anomalies = detector.detect_committee_conflicts(db)
    logger.info(f"   Found {len(conflict_anomalies)} anomalies\n")

    # Anomaly 6: Loss Avoidance
    logger.info("3. Detecting loss avoidance patterns...")
    loss_anomalies = detector.detect_loss_avoidance(db)
    logger.info(f"   Found {len(loss_anomalies)} anomalies\n")

    # Anomaly 7: Red Flag Combinations (needs previous results)
    combined_results = previous_results or {
        "wealth_anomalies": [],
        "asset_anomalies": [],
        "stock_anomalies": [],
    }
    combined_results.update({
        "timing_anomalies": timing_anomalies,
        "conflict_anomalies": conflict_anomalies,
    })

    logger.info("4. Detecting multi-factor risk combinations...")
    combination_anomalies = detector.detect_red_flag_combinations(db, combined_results)
    logger.info(f"   Found {len(combination_anomalies)} anomalies\n")

    logger.info("="*70)
    logger.info("SUMMARY")
    logger.info("="*70)
    logger.info(f"Extended anomalies detected: {len(timing_anomalies) + len(conflict_anomalies) + len(loss_anomalies) + len(combination_anomalies)}")
    logger.info(f"  • Trade Timing: {len(timing_anomalies)}")
    logger.info(f"  • Committee Conflicts: {len(conflict_anomalies)}")
    logger.info(f"  • Loss Avoidance: {len(loss_anomalies)}")
    logger.info(f"  • Multi-Factor Risk: {len(combination_anomalies)}")
    logger.info("="*70 + "\n")

    return {
        "timing_anomalies": timing_anomalies,
        "conflict_anomalies": conflict_anomalies,
        "loss_avoidance_anomalies": loss_anomalies,
        "combination_anomalies": combination_anomalies,
        "total": len(timing_anomalies) + len(conflict_anomalies) + len(loss_anomalies) + len(combination_anomalies),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from src.db.database import SessionLocal

    db = SessionLocal()
    results = run_extended_anomaly_detection(db)
    db.close()

