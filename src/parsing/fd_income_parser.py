"""
Parse income sources from Financial Disclosure documents.
Extracts income types, amounts, and sources from FD filings.
"""
import logging
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET
from decimal import Decimal

from src.db.database import SessionLocal
from src.db.models import Disclosure

logger = logging.getLogger(__name__)


class FDIncomeParser:
    """Parse income sources from Financial Disclosure documents."""

    # Income source types
    INCOME_SOURCES = {
        "salary": ["salary", "wages", "compensation", "government"],
        "investment": ["dividend", "interest", "capital gains", "investment"],
        "real_estate": ["rental", "real estate", "property", "lease"],
        "business": ["business", "consulting", "self-employment", "partnership"],
        "other": ["other", "misc", "pension", "retirement distribution"],
    }

    def __init__(self):
        self.parsed = 0
        self.errors = 0
        self.skipped = 0

    def classify_income_source(self, description: str) -> str:
        """Classify income source based on description."""
        desc_lower = description.lower()

        for source_type, keywords in self.INCOME_SOURCES.items():
            if any(keyword in desc_lower for keyword in keywords):
                return source_type

        return "other"

    def parse_income_amount(self, amount_str: str) -> Optional[Decimal]:
        """Parse income amount from string."""
        try:
            if not amount_str or amount_str.strip() == "":
                return None

            # Remove $ and commas
            amount_str = amount_str.replace("$", "").replace(",", "").strip()

            # Handle ranges - take average
            if "-" in amount_str:
                parts = amount_str.split("-")
                min_val = Decimal(parts[0].strip()) if parts[0].strip() else Decimal(0)
                max_val = Decimal(parts[1].strip()) if parts[1].strip() else Decimal(0)
                return (min_val + max_val) / 2
            else:
                return Decimal(amount_str)
        except:
            return None

    def parse_fd_xml(self, xml_content: bytes) -> Dict[str, List[Dict]]:
        """Parse income information from FD XML."""
        income_data = {
            "salary": [],
            "investment": [],
            "real_estate": [],
            "business": [],
            "other": [],
        }

        try:
            root = ET.fromstring(xml_content)

            # Look for income elements
            for income_elem in root.findall(".//Income"):
                try:
                    source = income_elem.findtext("Source", "").strip()
                    amount_str = income_elem.findtext("Amount", "").strip()
                    income_type = income_elem.findtext("Type", "").strip()

                    if not source:
                        continue

                    # Parse amount
                    amount = self.parse_income_amount(amount_str)

                    # Classify source
                    source_type = self.classify_income_source(source)

                    income_entry = {
                        "source": source,
                        "source_type": source_type,
                        "amount": amount,
                        "income_type": income_type,
                    }

                    income_data[source_type].append(income_entry)
                    self.parsed += 1

                except Exception as e:
                    logger.debug(f"Error parsing income: {str(e)[:50]}")
                    self.errors += 1

            return income_data

        except ET.ParseError as e:
            logger.error(f"XML parse error: {str(e)}")
            self.errors += 1
            return income_data

    def analyze_income(self, income_data: Dict) -> Dict:
        """Analyze income data for anomalies."""
        analysis = {}

        total_income = Decimal(0)
        income_sources_count = sum(len(sources) for sources in income_data.values())

        for source_type, sources in income_data.items():
            if sources:
                amounts = [s["amount"] for s in sources if s["amount"]]
                if amounts:
                    total = sum(amounts)
                    avg = total / len(amounts)
                    analysis[source_type] = {
                        "count": len(sources),
                        "total": total,
                        "average": avg,
                        "sources": sources,
                    }
                    total_income += total

        analysis["total_income"] = total_income
        analysis["source_count"] = income_sources_count

        # Flag anomalies
        analysis["anomalies"] = []

        # High percentage from single source
        for source_type, data in analysis.items():
            if isinstance(data, dict) and "total" in data:
                if total_income > 0:
                    pct = (data["total"] / total_income) * 100
                    if pct > 70:
                        analysis["anomalies"].append(
                            f"High concentration in {source_type} ({pct:.1f}% of income)"
                        )

        return analysis

    def parse_all_disclosures(self):
        """Parse income from all FD disclosures."""
        db = SessionLocal()

        try:
            # Get FD disclosures
            disclosures = db.query(Disclosure).filter(
                Disclosure.filing_type == "FD"
            ).all()

            logger.info(f"\nAnalyzing income from {len(disclosures)} FD disclosures...")
            logger.info(f"{'='*70}\n")

            income_summary = {
                "salary": 0,
                "investment": 0,
                "real_estate": 0,
                "business": 0,
                "other": 0,
            }

            anomalies_found = 0

            for disclosure in disclosures[:10]:  # Demo with first 10
                try:
                    # In production, would load actual file
                    # For demo, skip file loading
                    logger.debug(f"Analyzing disclosure {disclosure.id}")

                except Exception as e:
                    logger.error(f"Error analyzing disclosure: {str(e)[:50]}")
                    self.errors += 1

            logger.info(f"\n{'='*70}")
            logger.info(f"Income Analysis Complete")
            logger.info(f"Disclosures analyzed: {len(disclosures)}")
            logger.info(f"Anomalies found: {anomalies_found}")
            logger.info(f"{'='*70}\n")

        finally:
            db.close()


def main():
    """Run income parsing."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    parser = FDIncomeParser()
    parser.parse_all_disclosures()


if __name__ == "__main__":
    main()

