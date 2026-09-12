"""Trade anomaly analyzer for congressional stock transactions."""

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import Anomaly, Disclosure, Member, Transaction

logger = logging.getLogger(__name__)
_settings = get_settings()

# Sector keywords for classification
SECTOR_KEYWORDS = {
    "technology": [
        "tech",
        "software",
        "computer",
        "semiconductor",
        "chip",
        "apple",
        "microsoft",
        "google",
        "meta",
        "nvidia",
        "amd",
        "intel",
    ],
    "healthcare": [
        "health",
        "pharma",
        "biotech",
        "medical",
        "drug",
        "pfizer",
        "moderna",
        "johnson",
        "merck",
        "hospital",
    ],
    "finance": [
        "bank",
        "financial",
        "insurance",
        "capital",
        "goldman",
        "jpmorgan",
        "wells fargo",
        "citibank",
        "visa",
        "mastercard",
    ],
    "energy": ["oil", "gas", "energy", "exxon", "chevron", "shell", "solar", "wind", "renewable"],
    "defense": [
        "defense",
        "military",
        "aerospace",
        "lockheed",
        "raytheon",
        "boeing",
        "northrop",
        "general dynamics",
    ],
    "telecom": ["telecom", "communications", "at&t", "verizon", "t-mobile", "comcast"],
    "retail": ["retail", "amazon", "walmart", "target", "costco", "home depot"],
    "real_estate": ["real estate", "reit", "property", "housing"],
}

# Committee to sector mapping
COMMITTEE_SECTORS = {
    "finance": ["finance", "banking"],
    "technology": ["science", "technology", "commerce"],
    "healthcare": ["health", "labor", "veterans"],
    "defense": ["armed services", "defense", "intelligence", "homeland"],
    "energy": ["energy", "natural resources", "environment"],
}


class TradeAnalyzer:
    """
    Analyzes congressional stock trades for suspicious patterns.

    Detects:
    - Trades clustered before significant legislation/events
    - Unusual sector concentration
    - High trading frequency
    - Late PTR filings (>45 days)
    - Potential committee conflicts
    """

    def __init__(
        self,
        ptr_deadline_days: int = 45,
        min_trades_for_concentration: int = 5,
        concentration_threshold_percent: float = 50.0,
        frequency_threshold_per_month: int = 10,
        late_filing_min_days: int | None = None,
        late_filing_min_amount_usd: int | None = None,
    ):
        self.ptr_deadline_days = ptr_deadline_days
        self.min_trades_for_concentration = min_trades_for_concentration
        self.concentration_threshold_percent = concentration_threshold_percent
        self.frequency_threshold_per_month = frequency_threshold_per_month
        # The "late filing" detector previously fired on every PTR more than
        # 45 days late, generating thousands of low-signal anomalies. We now
        # require a much later filing AND a non-trivial transaction size.
        self.late_filing_min_days = (
            late_filing_min_days
            if late_filing_min_days is not None
            else _settings.late_filing_min_days
        )
        self.late_filing_min_amount_usd = (
            late_filing_min_amount_usd
            if late_filing_min_amount_usd is not None
            else _settings.late_filing_min_amount_usd
        )

    def _large_trade_asset_name(self, txn: Transaction) -> str:
        return txn.ticker or (txn.description[:20] if txn.description else "unknown")

    def _build_large_trade_text(self, txn: Transaction) -> Dict[str, str]:
        asset_name = self._large_trade_asset_name(txn)
        title = (
            f"Large transaction: {asset_name} {txn.transaction_type.value} (more than $1,000,000)"
        )
        description = (
            f"A {txn.transaction_type.value} of {asset_name} worth more than $1,000,000 was reported. "
            "Large transactions warrant additional scrutiny."
        )
        return {"title": title, "description": description}

    def _sync_large_trade_anomalies(self, db: Session, member_id: int | None = None) -> None:
        """Backfill transaction_id and sync title/description for large trades."""
        large_trade_threshold = Decimal("1000000")
        query = (
            db.query(Transaction)
            .join(Disclosure)
            .filter(Transaction.amount_min > large_trade_threshold)
        )
        if member_id:
            query = query.filter(Disclosure.member_id == member_id)

        for txn in query.all():
            disclosure = db.query(Disclosure).filter(Disclosure.id == txn.disclosure_id).first()
            if not disclosure:
                continue

            text = self._build_large_trade_text(txn)

            existing = (
                db.query(Anomaly)
                .filter(Anomaly.anomaly_type == "large_trade", Anomaly.transaction_id == txn.id)
                .first()
            )

            if existing:
                if (
                    existing.title != text["title"]
                    or existing.description != text["description"]
                    or existing.disclosure_id != disclosure.id
                ):
                    existing.title = text["title"]
                    existing.description = text["description"]
                    existing.disclosure_id = disclosure.id
                continue

            existing_no_txn = (
                db.query(Anomaly)
                .filter(
                    Anomaly.anomaly_type == "large_trade",
                    Anomaly.member_id == disclosure.member_id,
                    Anomaly.disclosure_id == disclosure.id,
                    Anomaly.title == text["title"],
                )
                .first()
            )

            if existing_no_txn:
                existing_no_txn.transaction_id = txn.id
                if existing_no_txn.description != text["description"]:
                    existing_no_txn.description = text["description"]

    def analyze_member(self, db: Session, member_id: int) -> List[Dict[str, Any]]:
        """
        Analyze a single member for trade anomalies.

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

        # Get all transactions for this member
        transactions = (
            db.query(Transaction)
            .join(Disclosure)
            .filter(Disclosure.member_id == member_id)
            .order_by(Transaction.transaction_date)
            .all()
        )

        if not transactions:
            return []

        self._sync_large_trade_anomalies(db, member_id=member_id)

        anomalies.extend(self._check_late_filings(db, member_id, member))
        anomalies.extend(self._check_sector_concentration(transactions, member_id, member))
        anomalies.extend(self._check_trading_frequency(transactions, member_id, member))
        anomalies.extend(self._check_large_trades(transactions, member_id, member))

        return anomalies

    def _check_late_filings(
        self, db: Session, member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Check for materially late PTR filings.

        Only flags trades where (filing_date - transaction_date) exceeds
        `late_filing_min_days` AND the transaction's max amount meets
        `late_filing_min_amount_usd`. These thresholds keep the signal
        meaningful — the raw "anything past the 45-day STOCK Act deadline"
        check produced thousands of small, noisy anomalies.
        """
        anomalies = []
        min_amount = Decimal(str(self.late_filing_min_amount_usd))

        ptr_disclosures = (
            db.query(Disclosure)
            .filter(
                Disclosure.member_id == member_id,
                Disclosure.is_ptr == True,
                Disclosure.parsed == True,
            )
            .all()
        )

        for disclosure in ptr_disclosures:
            transactions = (
                db.query(Transaction).filter(Transaction.disclosure_id == disclosure.id).all()
            )

            for txn in transactions:
                if not (txn.transaction_date and disclosure.filing_date):
                    continue

                days_to_file = (disclosure.filing_date - txn.transaction_date).days
                if days_to_file <= self.late_filing_min_days:
                    continue

                # Skip small trades — late filings on de minimis amounts are
                # mostly clerical and we don't want to flood the table.
                txn_amount = txn.amount_max or txn.amount_min
                if txn_amount is None or txn_amount < min_amount:
                    continue

                days_late = days_to_file - self.ptr_deadline_days
                if days_late <= 30:
                    severity = "low"
                    late_range = "moderately late (1-4 weeks)"
                elif days_late <= 90:
                    severity = "medium"
                    late_range = "significantly late (1-3 months)"
                else:
                    severity = "high"
                    late_range = "severely late (over 3 months)"

                anomalies.append(
                    {
                        "member_id": member_id,
                        "disclosure_id": disclosure.id,
                        "transaction_id": txn.id,
                        "anomaly_type": "late_filing",
                        "severity": severity,
                        "title": f"Late PTR filing: {late_range}",
                        "description": (
                            f"Transaction on {txn.transaction_date.strftime('%Y-%m-%d')} "
                            f"was filed {late_range} on "
                            f"{disclosure.filing_date.strftime('%Y-%m-%d')}. "
                            f"The STOCK Act requires filing within {self.ptr_deadline_days} days. "
                            f"Trade: {txn.transaction_type.value} {txn.ticker or txn.description[:30]}"
                        ),
                        "computed_value": Decimal(str(days_to_file)),
                        "threshold_value": Decimal(str(self.ptr_deadline_days)),
                    }
                )

        return anomalies

    def _check_sector_concentration(
        self, transactions: List[Transaction], member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Check if trades are unusually concentrated in a specific sector, per disclosure year."""
        anomalies = []

        # Group transactions by disclosure (by year)
        disclosures_map = defaultdict(list)

        for txn in transactions:
            if txn.disclosure_id:
                disclosures_map[txn.disclosure_id].append(txn)

        # Check each disclosure independently
        for disclosure_id, txns in disclosures_map.items():
            if len(txns) < self.min_trades_for_concentration:
                continue

            # Classify transactions by sector
            sector_counts = defaultdict(int)
            total_trades = 0

            for txn in txns:
                desc = (txn.description or "").lower() + " " + (txn.ticker or "").lower()

                for sector, keywords in SECTOR_KEYWORDS.items():
                    if any(kw in desc for kw in keywords):
                        sector_counts[sector] += 1
                        break
                else:
                    sector_counts["other"] += 1

                total_trades += 1

            # Check for concentration
            for sector, count in sector_counts.items():
                if sector == "other":
                    continue

                concentration_percent = (count / total_trades) * 100

                if concentration_percent > self.concentration_threshold_percent:
                    # Use vague ranges instead of exact percentages
                    if concentration_percent < 60:
                        concentration_range = "over 50%"
                    elif concentration_percent < 75:
                        concentration_range = "60-75%"
                    elif concentration_percent < 90:
                        concentration_range = "75-90%"
                    else:
                        concentration_range = "over 90%"

                    anomalies.append(
                        {
                            "member_id": member_id,
                            "disclosure_id": disclosure_id,
                            "anomaly_type": "sector_concentration",
                            "severity": min(10, 5 + int((concentration_percent - 50) / 10)),
                            "title": f"High concentration in {sector} sector ({concentration_range})",
                            "description": (
                                f"A significant portion of trades ({concentration_range}) "
                                f"are concentrated in the {sector} sector. This unusual concentration "
                                f"may warrant further review."
                            ),
                            "computed_value": Decimal(str(concentration_percent)),
                            "threshold_value": Decimal(str(self.concentration_threshold_percent)),
                        }
                    )

        return anomalies

    def _check_trading_frequency(
        self, transactions: List[Transaction], member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Check for unusually high trading frequency, per disclosure year."""
        anomalies = []

        if len(transactions) < 2:
            return []

        # Group transactions by disclosure first (by year), then by month
        disclosures_map = defaultdict(list)

        for txn in transactions:
            if txn.disclosure_id:
                disclosures_map[txn.disclosure_id].append(txn)

        # Check each disclosure independently
        for disclosure_id, txns in disclosures_map.items():
            # Group transactions by month within this disclosure
            monthly_counts = defaultdict(int)

            for txn in txns:
                if txn.transaction_date:
                    month_key = txn.transaction_date.strftime("%Y-%m")
                    monthly_counts[month_key] += 1

            # Check for high-frequency months
            for month, count in monthly_counts.items():
                if count > self.frequency_threshold_per_month:
                    # Format month from YYYY-MM to "Month Year"
                    try:
                        from datetime import datetime

                        date_obj = datetime.strptime(month, "%Y-%m")
                        formatted_month = date_obj.strftime("%B %Y")
                    except ValueError:
                        formatted_month = month

                    # Use vague ranges instead of exact counts
                    if count <= 15:
                        trade_range = "10-15"
                    elif count <= 25:
                        trade_range = "15-25"
                    elif count <= 50:
                        trade_range = "25-50"
                    elif count <= 100:
                        trade_range = "50-100"
                    else:
                        trade_range = "more than 100"

                    anomalies.append(
                        {
                            "member_id": member_id,
                            "disclosure_id": disclosure_id,
                            "anomaly_type": "high_trading_frequency",
                            "severity": min(10, 4 + (count - self.frequency_threshold_per_month)),
                            "title": f"High trading activity: {trade_range} trades in {formatted_month}",
                            "description": (
                                f"Between {trade_range} stock trades were made in {formatted_month}, "
                                f"which exceeds the threshold of {self.frequency_threshold_per_month} "
                                f"trades per month. High trading frequency may indicate "
                                f"active trading based on non-public information."
                            ),
                            "computed_value": Decimal(str(count)),
                            "threshold_value": Decimal(str(self.frequency_threshold_per_month)),
                        }
                    )

        return anomalies

    def _check_large_trades(
        self, transactions: List[Transaction], member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Flag unusually large trades (over $1M)."""
        anomalies = []
        large_trade_threshold = Decimal("1000000")  # $1M

        for txn in transactions:
            if txn.amount_min and txn.amount_min > large_trade_threshold:
                text = self._build_large_trade_text(txn)

                anomalies.append(
                    {
                        "member_id": member_id,
                        "disclosure_id": txn.disclosure_id,
                        "transaction_id": txn.id,
                        "anomaly_type": "large_trade",
                        "severity": min(10, 5 + int(txn.amount_min / Decimal("5000000"))),
                        "title": text["title"],
                        "description": text["description"],
                        "computed_value": txn.amount_min,
                        "threshold_value": large_trade_threshold,
                    }
                )

        return anomalies

    def analyze_all_members(self, db: Session) -> Dict[str, Any]:
        """
        Analyze all members for trade anomalies.

        Args:
            db: Database session

        Returns:
            Summary with all detected anomalies
        """
        from src.config import get_settings

        disabled_types = get_settings().disabled_anomaly_types_set

        members = db.query(Member).all()  # Include all members (active + retired)

        all_anomalies = []
        members_analyzed = 0
        members_with_anomalies = 0

        self._sync_large_trade_anomalies(db)

        for member in members:
            anomalies = self.analyze_member(db, member.id)
            members_analyzed += 1

            if anomalies:
                members_with_anomalies += 1

                for anomaly in anomalies:
                    # Check if this anomaly already exists
                    # For large_trade, check by transaction_id to ensure each transaction is unique
                    # For others, check by title within the same disclosure
                    if anomaly.get("transaction_id"):
                        existing = (
                            db.query(Anomaly)
                            .filter(
                                Anomaly.member_id == anomaly["member_id"],
                                Anomaly.anomaly_type == anomaly["anomaly_type"],
                                Anomaly.transaction_id == anomaly["transaction_id"],
                            )
                            .first()
                        )

                        if existing:
                            if (
                                existing.title != anomaly["title"]
                                or existing.description != anomaly["description"]
                                or existing.disclosure_id != anomaly.get("disclosure_id")
                            ):
                                existing.title = anomaly["title"]
                                existing.description = anomaly["description"]
                                existing.disclosure_id = anomaly.get("disclosure_id")
                            continue
                    else:
                        existing = (
                            db.query(Anomaly)
                            .filter(
                                Anomaly.member_id == anomaly["member_id"],
                                Anomaly.anomaly_type == anomaly["anomaly_type"],
                                Anomaly.disclosure_id == anomaly.get("disclosure_id"),
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
                        transaction_id=anomaly.get("transaction_id"),
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

        db.commit()

        return {
            "members_analyzed": members_analyzed,
            "members_with_anomalies": members_with_anomalies,
            "total_anomalies": len(all_anomalies),
            "anomalies": all_anomalies,
        }


def analyze_trades(db: Session, member_id: int | None = None) -> Dict[str, Any]:
    """
    Convenience function to run trade analysis.

    Args:
        db: Database session
        member_id: Optional specific member to analyze

    Returns:
        Analysis results
    """
    analyzer = TradeAnalyzer()

    if member_id:
        anomalies = analyzer.analyze_member(db, member_id)
        return {
            "member_id": member_id,
            "total_anomalies": len(anomalies),
            "anomalies": anomalies,
        }
    else:
        return analyzer.analyze_all_members(db)
