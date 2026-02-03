"""
Advanced Anomaly Detection for Congressional Financial Patterns

Focuses on three key indicators:
1. Net worth compared to salary
2. Stock performance above benchmarks
3. Rapid asset appreciation (e.g., business valuations)
"""
import logging
from typing import List, Dict, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.models import Member, Disclosure, Transaction, Asset, Anomaly

logger = logging.getLogger(__name__)

# Congressional salary by year
CONGRESSIONAL_SALARY_BY_YEAR = {
    2015: 174000,
    2016: 174000,
    2017: 174000,
    2018: 174000,
    2019: 174000,
    2020: 174000,
    2021: 180000,
    2022: 193500,
    2023: 193500,
    2024: 193500,
    2025: 193500,
    2026: 193500,
}


class AdvancedAnomalyDetector:
    """Detect advanced anomalies in Congressional finances."""

    def __init__(self):
        self.anomalies = []

    def get_salary_for_year(self, year: int) -> int:
        """Get congressional salary for a given year."""
        return CONGRESSIONAL_SALARY_BY_YEAR.get(year, 193500)

    # ========== ANOMALY 1: NET WORTH vs SALARY ==========

    def detect_wealth_vs_salary_anomalies(self, db: Session) -> List[Dict]:
        """
        Detect members whose net worth growth far exceeds what salary could explain.

        Example:
        - Member made $174k/year for 8 years = max $1.392M from salary
        - Member reported net worth grew from $500k to $5M in same period
        - Growth of $4.5M is 3.2x the salary contribution

        Returns: List of anomalies with severity levels
        """
        anomalies = []

        try:
            # Get all members with multiple years of FD data
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get disclosures ordered by year
                    disclosures = db.query(Disclosure).filter(
                        Disclosure.member_id == member.id,
                        Disclosure.filing_type == "FD"
                    ).order_by(Disclosure.filing_year).all()

                    if len(disclosures) < 2:
                        continue

                    # Calculate net worth for each year
                    wealth_progression = self._calculate_wealth_progression(db, disclosures)

                    if len(wealth_progression) < 2:
                        continue

                    # Compare to cumulative salary
                    first_year = wealth_progression[0]["year"]
                    last_year = wealth_progression[-1]["year"]
                    years_in_office = last_year - first_year + 1

                    # Calculate cumulative salary they could have saved
                    cumulative_salary = sum(
                        self.get_salary_for_year(year)
                        for year in range(first_year, last_year + 1)
                    )

                    # Get wealth values
                    first_wealth = wealth_progression[0]["net_worth_estimate"]
                    last_wealth = wealth_progression[-1]["net_worth_estimate"]

                    if not first_wealth or not last_wealth:
                        continue

                    wealth_growth = float(last_wealth) - float(first_wealth)

                    # Calculate what portion came from salary vs other sources
                    salary_contribution_ratio = cumulative_salary / wealth_growth if wealth_growth > 0 else 0

                    # Flag if wealth growth is 2x+ the total possible salary accumulation
                    if wealth_growth > cumulative_salary and salary_contribution_ratio < 0.5:
                        severity = self._calculate_wealth_severity(
                            wealth_growth,
                            cumulative_salary,
                            years_in_office
                        )

                        anomalies.append({
                            "member_id": member.id,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "chamber": member.chamber,
                            "anomaly_type": "wealth_vs_salary",
                            "severity": severity,
                            "years": f"{first_year}-{last_year}",
                            "initial_wealth": float(first_wealth),
                            "final_wealth": float(last_wealth),
                            "wealth_growth": wealth_growth,
                            "cumulative_salary": cumulative_salary,
                            "salary_contribution_ratio": salary_contribution_ratio,
                            "growth_multiple_of_salary": wealth_growth / cumulative_salary if cumulative_salary > 0 else 0,
                            "description": (
                                f"Net worth grew from ${first_wealth:,.0f} to ${last_wealth:,.0f} "
                                f"({wealth_growth:,.0f} total). Cumulative salary over {years_in_office} years: "
                                f"${cumulative_salary:,.0f}. Growth is {(wealth_growth/cumulative_salary):.1f}x "
                                f"total possible salary accumulation."
                            )
                        })

                except Exception as e:
                    logger.debug(f"Error analyzing {member.first_name} {member.last_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in wealth vs salary detection: {str(e)[:100]}")

        return anomalies

    def _calculate_wealth_progression(self, db: Session, disclosures: List[Disclosure]) -> List[Dict]:
        """Calculate estimated net worth for each year's disclosure."""
        progression = []

        for disclosure in disclosures:
            # Get all assets for this disclosure
            assets = db.query(Asset).filter(
                Asset.disclosure_id == disclosure.id
            ).all()

            # Calculate net worth from assets (use midpoint of value ranges)
            total_value = Decimal(0)
            for asset in assets:
                if asset.value_min and asset.value_max:
                    midpoint = (asset.value_min + asset.value_max) / 2
                    total_value += midpoint
                elif asset.value_max:
                    total_value += asset.value_max

            progression.append({
                "year": disclosure.filing_year,
                "disclosure_id": disclosure.id,
                "net_worth_estimate": total_value if total_value > 0 else None,
            })

        return progression

    def _calculate_wealth_severity(self, wealth_growth: float, salary: float, years: int) -> str:
        """Calculate severity level for wealth anomaly."""
        ratio = wealth_growth / salary if salary > 0 else 0

        if ratio > 5:
            return "CRITICAL"
        elif ratio > 3:
            return "HIGH"
        elif ratio > 1.5:
            return "MEDIUM"
        else:
            return "LOW"

    # ========== ANOMALY 2: ASSET APPRECIATION ==========

    def detect_asset_appreciation_anomalies(self, db: Session) -> List[Dict]:
        """
        Detect assets that appreciated dramatically in short time periods.

        Example: Ilhan Omar's winery business
        - 2021: $15,000
        - 2022: $1,000,000+
        - Growth: 6,567% in one year (highly suspicious)

        Returns: List of anomalies with timeline and growth metrics
        """
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    disclosures = db.query(Disclosure).filter(
                        Disclosure.member_id == member.id,
                        Disclosure.filing_type == "FD"
                    ).order_by(Disclosure.filing_year).all()

                    if len(disclosures) < 2:
                        continue

                    # Track assets year-over-year
                    assets_by_description = defaultdict(list)

                    for disclosure in disclosures:
                        assets = db.query(Asset).filter(
                            Asset.disclosure_id == disclosure.id
                        ).all()

                        for asset in assets:
                            # Track by description (to match same asset across years)
                            key = asset.description.lower().strip()

                            if asset.value_max:
                                assets_by_description[key].append({
                                    "year": disclosure.filing_year,
                                    "value": float(asset.value_max),
                                    "asset_type": asset.asset_type,
                                    "description": asset.description,
                                })

                    # Check for suspicious appreciation
                    for asset_desc, values in assets_by_description.items():
                        if len(values) < 2:
                            continue

                        values_sorted = sorted(values, key=lambda x: x["year"])

                        # Check each year-over-year change
                        for i in range(1, len(values_sorted)):
                            prev = values_sorted[i-1]
                            curr = values_sorted[i]

                            years_diff = curr["year"] - prev["year"]
                            value_growth = curr["value"] - prev["value"]

                            if prev["value"] > 0:
                                growth_percent = (value_growth / prev["value"]) * 100
                                annual_growth = growth_percent / years_diff if years_diff > 0 else growth_percent

                                # Flag if growth is >500% per year or >100% in single year
                                if (annual_growth > 500 or
                                    (years_diff == 1 and growth_percent > 100)):

                                    severity = "CRITICAL" if growth_percent > 1000 else "HIGH"

                                    anomalies.append({
                                        "member_id": member.id,
                                        "member_name": f"{member.first_name} {member.last_name}",
                                        "chamber": member.chamber,
                                        "anomaly_type": "rapid_asset_appreciation",
                                        "severity": severity,
                                        "asset_description": asset_desc,
                                        "asset_type": curr["asset_type"],
                                        "start_year": prev["year"],
                                        "end_year": curr["year"],
                                        "start_value": prev["value"],
                                        "end_value": curr["value"],
                                        "growth_amount": value_growth,
                                        "growth_percent": growth_percent,
                                        "annual_growth_rate": annual_growth,
                                        "description": (
                                            f"{asset_desc} grew from ${prev['value']:,.0f} ({prev['year']}) "
                                            f"to ${curr['value']:,.0f} ({curr['year']}). "
                                            f"Growth: {growth_percent:.0f}% in {years_diff} year(s) "
                                            f"({annual_growth:.0f}% annually). "
                                            f"Example: Similar to Ilhan Omar's winery business case."
                                        )
                                    })

                except Exception as e:
                    logger.debug(f"Error analyzing assets for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in asset appreciation detection: {str(e)[:100]}")

        return anomalies

    # ========== ANOMALY 3: STOCK PERFORMANCE vs BENCHMARKS ==========

    def detect_stock_outperformance_anomalies(self, db: Session, benchmark_annual_return: float = 0.10) -> List[Dict]:
        """
        Detect Congressional trading that outperforms market benchmarks.

        Benchmark: S&P 500 annual return (default 10%)
        Metric: Trading profits vs benchmark

        Example:
        - S&P 500 returned 10% in 2022
        - Member's trades returned 45% in same period
        - Suggests insider trading or exceptional timing

        Returns: List of members with suspicious trading performance
        """
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get all trades for this member
                    trades = db.query(Transaction).filter(
                        Transaction.member_id == member.id
                    ).all()

                    if not trades:
                        continue

                    # Group trades by year
                    trades_by_year = defaultdict(list)
                    for trade in trades:
                        year = trade.transaction_date.year
                        trades_by_year[year].append(trade)

                    # Analyze each year
                    for year, year_trades in trades_by_year.items():
                        year_anomaly = self._analyze_year_trading_performance(
                            member, year, year_trades, benchmark_annual_return
                        )

                        if year_anomaly:
                            anomalies.append(year_anomaly)

                except Exception as e:
                    logger.debug(f"Error analyzing trades for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in stock performance detection: {str(e)[:100]}")

        return anomalies

    def _analyze_year_trading_performance(
        self,
        member: Member,
        year: int,
        trades: List[Transaction],
        benchmark: float
    ) -> Optional[Dict]:
        """Analyze trading performance for a specific year."""

        if not trades:
            return None

        # Calculate estimated returns
        # Buy trades = negative (outflow), Sell trades = positive (inflow)
        buys = [t for t in trades if t.type == "purchase"]
        sells = [t for t in trades if t.type == "sale"]

        if not buys or not sells:
            return None

        total_bought = sum(Decimal(t.amount) if t.amount else Decimal(0) for t in buys)
        total_sold = sum(Decimal(t.amount) if t.amount else Decimal(0) for t in sells)

        if total_bought == 0:
            return None

        # Calculate simple return: (proceeds - costs) / costs
        gross_profit = total_sold - total_bought
        return_percent = float((gross_profit / total_bought) * 100) if total_bought > 0 else 0

        # Compare to benchmark
        excess_return = return_percent - (benchmark * 100)

        # Flag if return is 2x+ the benchmark (20% vs 10% expected)
        if excess_return > (benchmark * 100):
            severity = "CRITICAL" if excess_return > (benchmark * 200) else "HIGH"

            return {
                "member_id": member.id,
                "member_name": f"{member.first_name} {member.last_name}",
                "chamber": member.chamber,
                "anomaly_type": "outperforming_trades",
                "severity": severity,
                "year": year,
                "trades_count": len(trades),
                "buy_trades": len(buys),
                "sell_trades": len(sells),
                "total_invested": float(total_bought),
                "total_proceeds": float(total_sold),
                "gross_profit": float(gross_profit),
                "return_percent": return_percent,
                "benchmark_return": benchmark * 100,
                "excess_return": excess_return,
                "description": (
                    f"{member.first_name} {member.last_name} trading in {year}: "
                    f"Invested ${total_bought:,.0f}, received ${total_sold:,.0f}. "
                    f"Return: {return_percent:.1f}% vs benchmark {benchmark*100:.1f}%. "
                    f"Excess return: {excess_return:.1f}% ({excess_return/(benchmark*100):.1f}x benchmark). "
                    f"{len(buys)} buys, {len(sells)} sells."
                )
            }

        return None


def run_advanced_anomaly_detection(db: Session) -> Dict:
    """Run all three anomaly detection types."""
    detector = AdvancedAnomalyDetector()

    logger.info("\n" + "="*70)
    logger.info("ADVANCED ANOMALY DETECTION")
    logger.info("="*70 + "\n")

    # Anomaly 1: Wealth vs Salary
    logger.info("1. Detecting wealth vs salary anomalies...")
    wealth_anomalies = detector.detect_wealth_vs_salary_anomalies(db)
    logger.info(f"   Found {len(wealth_anomalies)} anomalies\n")

    # Anomaly 2: Asset Appreciation
    logger.info("2. Detecting rapid asset appreciation...")
    asset_anomalies = detector.detect_asset_appreciation_anomalies(db)
    logger.info(f"   Found {len(asset_anomalies)} anomalies\n")

    # Anomaly 3: Stock Performance
    logger.info("3. Detecting stock outperformance...")
    stock_anomalies = detector.detect_stock_outperformance_anomalies(db)
    logger.info(f"   Found {len(stock_anomalies)} anomalies\n")

    logger.info("="*70)
    logger.info("SUMMARY")
    logger.info("="*70)
    logger.info(f"Total anomalies detected: {len(wealth_anomalies) + len(asset_anomalies) + len(stock_anomalies)}")
    logger.info(f"  • Wealth/Salary: {len(wealth_anomalies)}")
    logger.info(f"  • Asset Appreciation: {len(asset_anomalies)}")
    logger.info(f"  • Stock Outperformance: {len(stock_anomalies)}")
    logger.info("="*70 + "\n")

    return {
        "wealth_anomalies": wealth_anomalies,
        "asset_anomalies": asset_anomalies,
        "stock_anomalies": stock_anomalies,
        "total": len(wealth_anomalies) + len(asset_anomalies) + len(stock_anomalies),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from src.db.database import SessionLocal

    db = SessionLocal()
    results = run_advanced_anomaly_detection(db)
    db.close()

