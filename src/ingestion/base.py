"""Base class for data ingestion."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class BaseIngester(ABC):
    """Abstract base class for data ingesters."""

    @abstractmethod
    def fetch_members(self) -> List[Dict[str, Any]]:
        """Fetch list of congressional members."""
        pass

    @abstractmethod
    def fetch_disclosures(self, member_id: str) -> List[Dict[str, Any]]:
        """Fetch disclosures for a specific member."""
        pass

    @abstractmethod
    def download_disclosure(self, disclosure_url: str, output_path: str) -> bool:
        """Download a disclosure document."""
        pass

