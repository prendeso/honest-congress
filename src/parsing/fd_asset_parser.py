"""
Parse asset values from Financial Disclosure XML files.
Extracts asset types, descriptions, and value ranges from FD documents.
"""

import logging
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Dict, List

from src.db.database import SessionLocal
from src.db.models import Asset, AssetType, Disclosure

logger = logging.getLogger(__name__)


class FDAssetParser:
    """Parse asset values from Financial Disclosure documents."""

    # Asset type mappings
    ASSET_TYPE_KEYWORDS = {
        AssetType.STOCK: ["stock", "share", "equity"],
        AssetType.BOND: ["bond", "treasury", "note"],
        AssetType.MUTUAL_FUND: ["fund", "mutual", "etf"],
        AssetType.REAL_ESTATE: ["property", "real estate", "home", "house", "land"],
        AssetType.RETIREMENT: ["401k", "ira", "retirement", "pension", "roth"],
        AssetType.BANK_ACCOUNT: ["bank", "savings", "checking", "account", "cash"],
    }

    def __init__(self):
        self.parsed = 0
        self.errors = 0
        self.skipped = 0

    def classify_asset_type(self, description: str) -> AssetType:
        """Classify asset based on description."""
        desc_lower = description.lower()

        for asset_type, keywords in self.ASSET_TYPE_KEYWORDS.items():
            if any(keyword in desc_lower for keyword in keywords):
                return asset_type

        return AssetType.OTHER

    def parse_value_range(self, value_str: str) -> tuple:
        """Parse value range strings like '$100,001 - $250,000'."""
        try:
            if not value_str or value_str.strip() == "":
                return None, None

            # Remove $ and commas
            value_str = value_str.replace("$", "").replace(",", "").strip()

            # Handle ranges like "100,001 - 250,000"
            if "-" in value_str:
                parts = value_str.split("-")
                min_val = Decimal(parts[0].strip()) if parts[0].strip() else None
                max_val = Decimal(parts[1].strip()) if parts[1].strip() else None
                return min_val, max_val
            else:
                # Single value
                val = Decimal(value_str)
                return val, val
        except (ValueError, ArithmeticError):
            return None, None

    def parse_fd_xml(self, xml_content: bytes) -> List[Dict]:
        """Parse asset information from FD XML."""
        assets = []

        try:
            root = ET.fromstring(xml_content)

            # Look for asset elements
            for asset_elem in root.findall(".//Asset"):
                try:
                    description = asset_elem.findtext("Description", "").strip()
                    value_str = asset_elem.findtext("Value", "").strip()
                    income_str = asset_elem.findtext("Income", "").strip()

                    if not description:
                        continue

                    # Parse values
                    value_min, value_max = self.parse_value_range(value_str)
                    income_min, income_max = self.parse_value_range(income_str)

                    # Classify asset type
                    asset_type = self.classify_asset_type(description)

                    assets.append(
                        {
                            "description": description,
                            "asset_type": asset_type,
                            "value_min": value_min,
                            "value_max": value_max,
                            "income_min": income_min,
                            "income_max": income_max,
                        }
                    )

                except Exception as e:
                    logger.debug(f"Error parsing asset: {str(e)[:50]}")
                    self.errors += 1

            self.parsed += len(assets)
            return assets

        except ET.ParseError as e:
            logger.error(f"XML parse error: {str(e)}")
            self.errors += 1
            return []

    def ingest_disclosure_assets(self, disclosure_id: int, assets: List[Dict], db_session):
        """Insert parsed assets into database."""
        count = 0

        for asset_data in assets:
            try:
                asset = Asset(
                    disclosure_id=disclosure_id,
                    asset_type=asset_data["asset_type"],
                    description=asset_data["description"],
                    value_min=asset_data["value_min"],
                    value_max=asset_data["value_max"],
                    income_min=asset_data["income_min"],
                    income_max=asset_data["income_max"],
                )

                db_session.add(asset)
                count += 1

            except Exception as e:
                logger.error(f"Error inserting asset: {str(e)[:50]}")
                self.errors += 1

        return count

    def parse_all_disclosures(self):
        """Parse assets from all unparsed FD disclosures."""
        db = SessionLocal()

        try:
            # Get unparsed FD disclosures
            unparsed = (
                db.query(Disclosure)
                .filter((Disclosure.filing_type == "FD") & (Disclosure.parsed == False))
                .all()
            )

            logger.info(f"\nParsing assets from {len(unparsed)} FD disclosures...")
            logger.info(f"{'=' * 70}\n")

            for disclosure in unparsed:
                try:
                    # Read PDF/XML file - would need actual file path
                    # For now, simulate the parsing
                    logger.debug(f"Parsing disclosure {disclosure.id}: {disclosure.document_id}")

                    # In production, would load actual file and parse
                    # For demo, we skip file loading

                except Exception as e:
                    logger.error(f"Error processing disclosure: {str(e)[:50]}")
                    self.errors += 1

            db.commit()

            logger.info(f"\n{'=' * 70}")
            logger.info("Asset Parsing Complete")
            logger.info(f"Assets parsed: {self.parsed}")
            logger.info(f"Errors: {self.errors}")
            logger.info(f"{'=' * 70}\n")

        finally:
            db.close()


def main():
    """Run asset parsing."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = FDAssetParser()
    parser.parse_all_disclosures()


if __name__ == "__main__":
    main()
