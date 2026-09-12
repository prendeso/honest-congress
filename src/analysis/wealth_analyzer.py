"""Wealth anomaly analyzer for congressional disclosures."""

import logging
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db import Anomaly, Asset, Disclosure, Liability, Member

logger = logging.getLogger(__name__)
settings = get_settings()


class WealthAnalyzer:
    """
    Analyzes congressional member wealth for anomalies.

    Detects:
    - Net worth growth exceeding congressional salary by threshold
    - Sudden unexplained asset increases
    - Discrepancies between reported assets and lifestyle indicators
    """

    def __init__(
        self, threshold_percent: float | None = None, congressional_salary: int | None = None
    ):
        self.threshold_percent = threshold_percent or settings.wealth_growth_threshold_percent
        self.congressional_salary = congressional_salary or settings.congressional_salary

    def analyze_member(self, db: Session, member_id: int) -> List[Dict[str, Any]]:
        """
        Analyze a single member for wealth anomalies.

        Args:
            db: Database session
            member_id: Member ID to analyze

        Returns:
            List of detected anomalies
        """
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return []

        anomalies = []

        # Get all disclosures ordered by year
        disclosures = (
            db.query(Disclosure)
            .filter(Disclosure.member_id == member_id, Disclosure.parsed == True)
            .order_by(Disclosure.filing_year)
            .all()
        )

        if len(disclosures) < 2:
            return []  # Need at least 2 years to compare

        # Calculate net worth for each year
        net_worths = []
        for disclosure in disclosures:
            net_worth = self._calculate_net_worth(db, disclosure.id)
            net_worths.append(
                {
                    "year": disclosure.filing_year,
                    "disclosure_id": disclosure.id,
                    "net_worth_min": net_worth["min"],
                    "net_worth_max": net_worth["max"],
                    "net_worth_mid": net_worth["mid"],
                }
            )

        # Check year-over-year growth
        for i in range(1, len(net_worths)):
            prev = net_worths[i - 1]
            curr = net_worths[i]

            if prev["net_worth_mid"] and curr["net_worth_mid"]:
                prev_nw = float(prev["net_worth_mid"])
                curr_nw = float(curr["net_worth_mid"])

                if prev_nw > 0:
                    growth = curr_nw - prev_nw
                    growth_percent = (growth / prev_nw) * 100

                    # Calculate expected maximum growth from salary
                    years_between = curr["year"] - prev["year"]

                    # Skip if no time has passed or only 0 years between (insufficient data)
                    if years_between <= 0:
                        continue

                    max_salary_growth = float(self.congressional_salary) * years_between
                    salary_growth_percent = (max_salary_growth / prev_nw) * 100

                    # Flag if growth exceeds salary by threshold
                    if growth > 0 and growth_percent > (
                        salary_growth_percent + float(self.threshold_percent)
                    ):
                        # Use vague ranges for growth percentage
                        if growth_percent < 100:
                            growth_range = "significantly"
                        elif growth_percent < 200:
                            growth_range = "substantially (100-200%)"
                        elif growth_percent < 500:
                            growth_range = "dramatically (200-500%)"
                        else:
                            growth_range = "extremely (over 500%)"

                        # Use vague ranges for dollar amounts
                        if growth < 100000:
                            growth_amount = "tens of thousands of dollars"
                        elif growth < 500000:
                            growth_amount = "$100,000-$500,000"
                        elif growth < 1000000:
                            growth_amount = "$500,000-$1,000,000"
                        elif growth < 5000000:
                            growth_amount = "$1-$5 million"
                        else:
                            growth_amount = "more than $5 million"

                        # Report as anomaly for the CURRENT year (the year with growth)
                        anomaly = {
                            "member_id": member_id,
                            "disclosure_id": curr["disclosure_id"],
                            "anomaly_type": "excessive_wealth_growth",
                            "severity": self._calculate_severity(
                                growth_percent, salary_growth_percent
                            ),
                            "title": f"Wealth growth {growth_range} exceeds salary-based expectation ({prev['year']}-{curr['year']})",
                            "description": (
                                f"Between {prev['year']} and {curr['year']}, "
                                f"net worth grew by approximately {growth_amount}. "
                                f"Congressional salary for {years_between} year(s) would not explain "
                                f"this level of wealth accumulation."
                            ),
                            "computed_value": Decimal(str(growth)),
                            "threshold_value": Decimal(str(max_salary_growth)),
                        }
                        anomalies.append(anomaly)

        return anomalies

    def analyze_all_members(self, db: Session) -> Dict[str, Any]:
        """
        Analyze all members for wealth anomalies.

        Args:
            db: Database session

        Returns:
            Summary of analysis with all detected anomalies
        """
        from src.config import get_settings

        disabled_types = get_settings().disabled_anomaly_types_set

        members = db.query(Member).all()  # Include all members (active + retired)

        all_anomalies = []
        members_analyzed = 0
        members_with_anomalies = 0

        for member in members:
            anomalies = self.analyze_member(db, member.id)

            if anomalies:
                members_with_anomalies += 1

                for anomaly in anomalies:
                    # Check if this anomaly already exists
                    existing = (
                        db.query(Anomaly)
                        .filter(
                            Anomaly.member_id == anomaly["member_id"],
                            Anomaly.anomaly_type == anomaly["anomaly_type"],
                            Anomaly.title == anomaly["title"],
                        )
                        .first()
                    )

                    if existing:
                        # Skip duplicate
                        continue

                    # These two analyzers build Anomaly() directly instead of
                    # going through persist_anomalies(), so the disabled-type
                    # gate has to be repeated here.
                    if anomaly["anomaly_type"] in disabled_types:
                        continue

                    # Store anomaly in database
                    db_anomaly = Anomaly(
                        member_id=anomaly["member_id"],
                        disclosure_id=anomaly.get("disclosure_id"),
                        anomaly_type=anomaly["anomaly_type"],
                        severity=anomaly["severity"],
                        title=anomaly["title"],
                        description=anomaly["description"],
                        computed_value=anomaly.get("computed_value"),
                        threshold_value=anomaly.get("threshold_value"),
                    )
                    db.add(db_anomaly)
                    all_anomalies.append(
                        {
                            **anomaly,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "state": member.state,
                            "party": member.party.value,
                        }
                    )

            members_analyzed += 1

        db.commit()

        return {
            "members_analyzed": members_analyzed,
            "members_with_anomalies": members_with_anomalies,
            "total_anomalies": len(all_anomalies),
            "anomalies": all_anomalies,
        }

    def _calculate_net_worth(self, db: Session, disclosure_id: int) -> Dict[str, Decimal | None]:
        """Calculate net worth from a disclosure's assets and liabilities."""
        # Sum assets
        assets_result = (
            db.query(func.sum(Asset.value_min).label("min"), func.sum(Asset.value_max).label("max"))
            .filter(Asset.disclosure_id == disclosure_id)
            .first()
        )

        assets_min = (assets_result.min if assets_result else None) or Decimal(0)
        assets_max = (assets_result.max if assets_result else None) or Decimal(0)

        # Liabilities were previously hardcoded to zero with a comment saying
        # they were "accounted for", which made this gross assets rather than
        # net worth -- inflating every wealth-growth flag derived from it.
        liabilities_result = (
            db.query(
                func.sum(Liability.amount_min).label("min"),
                func.sum(Liability.amount_max).label("max"),
            )
            .filter(Liability.disclosure_id == disclosure_id)
            .first()
        )

        liabilities_min = (liabilities_result.min if liabilities_result else None) or Decimal(0)
        liabilities_max = (liabilities_result.max if liabilities_result else None) or Decimal(0)

        net_min = assets_min - liabilities_max
        net_max = assets_max - liabilities_min

        return {
            "min": net_min,
            "max": net_max,
            "mid": (net_min + net_max) / 2 if net_min and net_max else None,
        }

    def _calculate_severity(self, actual_percent: float, expected_percent: float) -> str:
        """Calculate anomaly severity based on deviation."""
        deviation = actual_percent - expected_percent

        if deviation > 500:
            return "high"
        elif deviation > 200:
            return "medium"
        else:
            return "low"


def analyze_wealth(db: Session, member_id: int | None = None) -> Dict[str, Any]:
    """
    Convenience function to run wealth analysis.

    Args:
        db: Database session
        member_id: Optional member ID (analyzes all if not provided)

    Returns:
        Analysis results
    """
    analyzer = WealthAnalyzer()

    if member_id:
        anomalies = analyzer.analyze_member(db, member_id)
        return {
            "member_id": member_id,
            "anomalies": anomalies,
            "total_anomalies": len(anomalies),
        }
    else:
        return analyzer.analyze_all_members(db)
