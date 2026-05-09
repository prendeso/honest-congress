"""
Map committee assignments and detect trading conflicts.
Links members to committees and identifies sector conflicts.
"""

import logging
from typing import Dict, List, Set

from sqlalchemy.orm import Session

from src.db.database import SessionLocal
from src.db.models import Member, Transaction

logger = logging.getLogger(__name__)


class CommitteeConflictMapper:
    """Map committees and detect trading conflicts."""

    # Committee to sector mapping
    COMMITTEE_SECTOR_MAP = {
        # Finance committees
        "finance": ["finance", "banking", "insurance", "capital", "jpmorgan", "wells fargo"],
        "banking": ["bank", "financial", "credit", "visa", "mastercard"],
        "appropriations": ["defense", "weapons", "lockheed", "boeing", "raytheon"],
        # Technology committees
        "science": ["tech", "software", "semiconductor", "intel", "nvidia", "apple"],
        "commerce": ["internet", "telecom", "wireless", "at&t", "verizon", "comcast"],
        "energy": ["energy", "oil", "gas", "exxon", "chevron", "renewables"],
        # Healthcare committees
        "health": ["pharma", "biotech", "hospital", "pfizer", "moderna", "merck"],
        "labor": ["healthcare", "medical", "insurance", "wellness"],
        # Defense committees
        "armed_services": ["defense", "military", "aerospace", "weapons", "lockheed"],
        "intelligence": ["defense", "security", "intel"],
        "homeland_security": ["security", "defense", "safety"],
        # Environment/Energy
        "environment": ["energy", "renewable", "environmental", "solar", "wind"],
        "natural_resources": ["oil", "gas", "mining", "energy"],
        # Agriculture
        "agriculture": ["food", "agriculture", "farming", "monsanto"],
        # Veterans
        "veterans": ["defense", "weapons", "military", "healthcare"],
    }

    # Known committee assignments (would come from Congress.gov in production)
    SAMPLE_COMMITTEE_ASSIGNMENTS = {
        # This would be populated from Congress.gov API
        # Format: member_bioguide_id -> ["committee1", "committee2", ...]
    }

    def __init__(self):
        self.mapped = 0
        self.conflicts_found = 0
        self.errors = 0

    def get_committee_sectors(self, committee: str) -> Set[str]:
        """Get sectors covered by a committee."""
        committee_lower = committee.lower()

        for comm_key, sectors in self.COMMITTEE_SECTOR_MAP.items():
            if comm_key in committee_lower:
                return set(sectors)

        return set()

    def classify_ticker_sector(self, ticker: str, description: str = "") -> str | None:
        """Classify ticker to sector."""
        # Map specific companies to sectors
        company_sectors = {
            # Tech
            "AAPL": "tech",
            "MSFT": "tech",
            "GOOGL": "tech",
            "META": "tech",
            "NVDA": "tech",
            "TSLA": "tech",
            "INTC": "tech",
            "AMD": "tech",
            # Finance
            "JPM": "finance",
            "GS": "finance",
            "BAC": "finance",
            "WFC": "finance",
            "V": "finance",
            "MA": "finance",
            # Defense
            "LMT": "defense",
            "BA": "defense",
            "RTX": "defense",
            "NOC": "defense",
            # Energy
            "XOM": "energy",
            "CVX": "energy",
            "COP": "energy",
            # Healthcare
            "PFE": "healthcare",
            "JNJ": "healthcare",
            "MRK": "healthcare",
            "MRNA": "healthcare",
            # Telecom
            "T": "telecom",
            "VZ": "telecom",
            "CMCSA": "telecom",
        }

        ticker_upper = ticker.upper()
        if ticker_upper in company_sectors:
            return company_sectors[ticker_upper]

        return None

    def detect_conflicts(
        self, member_id: int, committees: List[str], db_session: Session
    ) -> List[Dict]:
        """Detect trading conflicts for a member."""
        conflicts = []

        try:
            # Get member
            member = db_session.query(Member).filter(Member.id == member_id).first()
            if not member:
                return conflicts

            # Get member's trades
            trades = db_session.query(Transaction).filter(Transaction.member_id == member_id).all()

            # Get committee sectors
            committee_sectors = set()
            for committee in committees:
                committee_sectors.update(self.get_committee_sectors(committee))

            # Check for conflicts
            for trade in trades:
                # Classify trade sector
                trade_sector = self.classify_ticker_sector(trade.ticker)

                if trade_sector and trade_sector in committee_sectors:
                    conflicts.append(
                        {
                            "member": member,
                            "committee": [c for c in committees if self.get_committee_sectors(c)],
                            "ticker": trade.ticker,
                            "sector": trade_sector,
                            "date": trade.transaction_date,
                            "type": "potential_conflict",
                        }
                    )
                    self.conflicts_found += 1

        except Exception as e:
            logger.error(f"Error detecting conflicts: {str(e)[:50]}")
            self.errors += 1

        return conflicts

    def map_committees(self):
        """Map committees for all members."""
        db = SessionLocal()

        try:
            logger.info("\nMapping committee assignments...")
            logger.info(f"{'=' * 70}\n")

            members = db.query(Member).all()

            total_mapped = 0

            for member in members:
                try:
                    # In production, fetch from Congress.gov API
                    # For demo, using sample assignments

                    committees = self.SAMPLE_COMMITTEE_ASSIGNMENTS.get(member.bioguide_id, [])

                    if committees:
                        # Detect conflicts
                        conflicts = self.detect_conflicts(member.id, committees, db)
                        total_mapped += 1

                        if conflicts:
                            logger.info(
                                f"{member.first_name} {member.last_name}: {len(conflicts)} potential conflicts"
                            )

                except Exception as e:
                    logger.error(
                        f"Error mapping {member.first_name} {member.last_name}: {str(e)[:50]}"
                    )
                    self.errors += 1

            logger.info(f"\n{'=' * 70}")
            logger.info("Committee Mapping Complete")
            logger.info(f"Members mapped: {total_mapped}")
            logger.info(f"Conflicts detected: {self.conflicts_found}")
            logger.info(f"Errors: {self.errors}")
            logger.info(f"{'=' * 70}\n")

            logger.info("NOTE: Committee data would be fetched from Congress.gov API in production")

        finally:
            db.close()

    def get_sample_sectors(self) -> Dict[str, List[str]]:
        """Get sample sector mapping."""
        return self.COMMITTEE_SECTOR_MAP


def main():
    """Run committee mapping."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    mapper = CommitteeConflictMapper()
    mapper.map_committees()


if __name__ == "__main__":
    main()
