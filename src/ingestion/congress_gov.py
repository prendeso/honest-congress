"""Congress.gov API and alternative sources for member metadata.

Replaces the deprecated ProPublica Congress API with:
1. unitedstates.io GitHub - Community-maintained GitHub project (primary, no API key needed)
2. Congress.gov API - Official API (backup, requires free API key)
"""
import requests
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.config import get_settings

logger = logging.getLogger(__name__)

# Primary: unitedstates.io GitHub (free, no API key needed, community-maintained)
UNITEDSTATES_LEGISLATORS_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages/legislators-current.json"
UNITEDSTATES_HISTORICAL_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages/legislators-historical.json"

# Backup: Congress.gov API (free but requires API key from api.congress.gov)
CONGRESS_GOV_BASE_URL = "https://api.congress.gov/v3"


class CongressGovClient:
    """
    Client for fetching member metadata from alternative sources.

    Uses multiple data sources:
    1. unitedstates.io GitHub GitHub project (primary - most reliable, no API key needed)
    2. Congress.gov API (backup, requires free API key)

    Get a free Congress.gov API key at: https://api.congress.gov/sign-up/
    """

    def __init__(self, api_key: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.congress_gov_api_key

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HonestCongress/1.0 (Congressional Disclosure Analyzer)",
            "Accept": "application/json",
        })

        # Cache for member data
        self._members_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = 3600  # 1 hour cache

    def get_current_members(self, chamber: str = "both") -> List[Dict[str, Any]]:
        """
        Get all current members of Congress.

        Args:
            chamber: "house", "senate", or "both"

        Returns:
            List of member metadata dicts
        """
        # Check cache
        if self._is_cache_valid():
            members = self._members_cache
        else:
            # Try unitedstates.io GitHub first (most reliable)
            members = self._fetch_from_unitedstates()

            if not members:
                # Fallback to Congress.gov API
                logger.warning("Primary source failed, falling back to Congress.gov API")
                members = self._fetch_from_congress_gov()

            # Update cache
            self._members_cache = members
            self._cache_time = datetime.now()

        # Filter by chamber if specified
        if chamber != "both":
            chamber_filter = chamber.lower()
            members = [m for m in members if m["chamber"] == chamber_filter]

        logger.info(f"Returning {len(members)} members (chamber={chamber})")
        return members

    def get_all_members(self, chamber: str = "both") -> List[Dict[str, Any]]:
        """
        Get ALL members of Congress (current + historical, active + retired).

        Args:
            chamber: "house", "senate", or "both"

        Returns:
            List of member metadata dicts (includes retired members)
        """
        logger.info("Fetching all members (current + historical)...")

        # Fetch current members
        current_members = self._fetch_from_unitedstates()

        # Fetch historical members
        historical_members = self._fetch_historical_from_unitedstates()

        # Merge by bioguide_id, current takes precedence
        members_by_id = {}

        # Add historical first (will be overwritten by current if exists)
        for m in historical_members:
            if m.get("bioguide_id"):
                members_by_id[m["bioguide_id"]] = m

        # Add current (overwrites historical for same bioguide_id)
        for m in current_members:
            if m.get("bioguide_id"):
                members_by_id[m["bioguide_id"]] = m

        members = list(members_by_id.values())

        # Filter by chamber if specified
        if chamber != "both":
            chamber_filter = chamber.lower()
            members = [m for m in members if m["chamber"] == chamber_filter]

        logger.info(f"Returning {len(members)} total members (current + historical, chamber={chamber})")
        return members

    def _fetch_historical_from_unitedstates(self) -> List[Dict[str, Any]]:
        """
        Fetch historical (retired) legislators from unitedstates.io GitHub project.
        """
        try:
            logger.info("Fetching historical legislators from unitedstates.io...")
            response = self.session.get(UNITEDSTATES_HISTORICAL_URL, timeout=60)
            response.raise_for_status()

            data = response.json()
            members = []

            for legislator in data:
                terms = legislator.get("terms", [])
                if not terms:
                    continue

                # Use most recent term for info
                latest_term = terms[-1]

                # Extract member info
                name = legislator.get("name", {})
                ids = legislator.get("id", {})

                # Determine chamber from latest term type
                term_type = latest_term.get("type", "")
                chamber = "senate" if term_type == "sen" else "house"

                members.append({
                    "bioguide_id": ids.get("bioguide", ""),
                    "first_name": name.get("first", ""),
                    "last_name": name.get("last", ""),
                    "full_name": f"{name.get('first', '')} {name.get('last', '')}".strip(),
                    "chamber": chamber,
                    "party": self._normalize_party(latest_term.get("party", "")),
                    "state": latest_term.get("state", ""),
                    "district": str(latest_term.get("district", "")) if latest_term.get("district") else None,
                    "in_office": False,  # Historical = retired
                    "start_date": latest_term.get("start"),
                    "end_date": latest_term.get("end"),
                })

            logger.info(f"Fetched {len(members)} historical members from unitedstates.io")
            return members

        except requests.RequestException as e:
            logger.error(f"Failed to fetch historical members: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse historical members data: {e}")
            return []

    def _is_cache_valid(self) -> bool:
        """Check if the member cache is still valid."""
        if self._members_cache is None or self._cache_time is None:
            return False
        elapsed = (datetime.now() - self._cache_time).total_seconds()
        return elapsed < self._cache_ttl

    def _fetch_from_unitedstates(self) -> List[Dict[str, Any]]:
        """
        Fetch current legislators from unitedstates.io GitHub project.

        This is a well-maintained GitHub project with current congressional data.
        Repository: https://github.com/unitedstates/congress-legislators
        """
        try:
            response = self.session.get(UNITEDSTATES_LEGISLATORS_URL, timeout=30)
            response.raise_for_status()

            data = response.json()
            members = []

            for legislator in data:
                # Get current term
                terms = legislator.get("terms", [])
                if not terms:
                    continue

                current_term = terms[-1]  # Most recent term

                # Check if currently in office
                end_date = current_term.get("end")
                if end_date:
                    try:
                        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                        if end_dt < datetime.now():
                            continue  # Term ended
                    except ValueError:
                        pass

                # Extract member info
                name = legislator.get("name", {})
                ids = legislator.get("id", {})

                # Determine chamber from term type
                term_type = current_term.get("type", "")
                chamber = "senate" if term_type == "sen" else "house"

                members.append({
                    "bioguide_id": ids.get("bioguide", ""),
                    "first_name": name.get("first", ""),
                    "last_name": name.get("last", ""),
                    "full_name": f"{name.get('first', '')} {name.get('last', '')}".strip(),
                    "chamber": chamber,
                    "party": self._normalize_party(current_term.get("party", "")),
                    "state": current_term.get("state", ""),
                    "district": str(current_term.get("district", "")) if current_term.get("district") else None,
                    "in_office": True,
                    "start_date": current_term.get("start"),
                    "end_date": current_term.get("end"),
                    "url": current_term.get("url"),
                    "office": current_term.get("office"),
                    "phone": current_term.get("phone"),
                })

            logger.info(f"Fetched {len(members)} members from unitedstates.io GitHub")
            return members

        except requests.RequestException as e:
            logger.error(f"Failed to fetch from unitedstates.io GitHub: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse unitedstates.io GitHub data: {e}")
            return []

    def _fetch_from_congress_gov(self) -> List[Dict[str, Any]]:
        """
        Fetch members from Congress.gov API.

        Requires API key for access. Get one free at: https://api.congress.gov/sign-up/
        """
        if not self.api_key:
            logger.warning(
                "No Congress.gov API key configured. "
                "Get a free key at https://api.congress.gov/sign-up/"
            )
            return []

        members = []

        try:
            url = f"{CONGRESS_GOV_BASE_URL}/member"
            params = {
                "currentMember": "true",
                "limit": 250,
                "api_key": self.api_key,
            }

            # Paginate through all results
            offset = 0
            while True:
                params["offset"] = offset
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()
                batch = data.get("members", [])

                if not batch:
                    break

                for member in batch:
                    # Determine chamber from terms
                    terms = member.get("terms", {}).get("item", [])
                    current_chamber = "house"
                    for term in reversed(terms):
                        chamber_name = term.get("chamber", "").lower()
                        if "senate" in chamber_name:
                            current_chamber = "senate"
                            break
                        elif "house" in chamber_name:
                            current_chamber = "house"
                            break

                    members.append({
                        "bioguide_id": member.get("bioguideId", ""),
                        "first_name": member.get("firstName", ""),
                        "last_name": member.get("lastName", ""),
                        "full_name": member.get("name", ""),
                        "chamber": current_chamber,
                        "party": self._normalize_party(member.get("partyName", "")),
                        "state": member.get("state", ""),
                        "district": member.get("district"),
                        "in_office": True,
                        "start_date": None,
                        "url": member.get("url"),
                    })

                # Check if there are more results
                pagination = data.get("pagination", {})
                total = pagination.get("count", 0)
                offset += len(batch)

                if offset >= total:
                    break

            logger.info(f"Fetched {len(members)} members from Congress.gov API")
            return members

        except requests.RequestException as e:
            logger.error(f"Failed to fetch from Congress.gov: {e}")
            return []

    def get_member_by_id(self, bioguide_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific member.

        Args:
            bioguide_id: Member's Bioguide ID (e.g., "P000197" for Pelosi)

        Returns:
            Member metadata dict or None
        """
        # Search in current members first
        members = self.get_current_members()
        for m in members:
            if m.get("bioguide_id") == bioguide_id:
                return m

        # Try Congress.gov API for historical/detailed lookup
        if self.api_key:
            try:
                url = f"{CONGRESS_GOV_BASE_URL}/member/{bioguide_id}"
                params = {"api_key": self.api_key}

                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()
                member = data.get("member", {})

                if member:
                    terms = member.get("terms", {}).get("item", [])
                    current_term = terms[-1] if terms else {}
                    chamber = current_term.get("chamber", "").lower()

                    return {
                        "bioguide_id": member.get("bioguideId", bioguide_id),
                        "first_name": member.get("firstName", ""),
                        "last_name": member.get("lastName", ""),
                        "full_name": member.get("directOrderName", ""),
                        "chamber": "senate" if "senate" in chamber else "house",
                        "party": self._normalize_party(member.get("partyHistory", [{}])[-1].get("partyName", "")),
                        "state": member.get("state", ""),
                        "district": current_term.get("district"),
                        "in_office": member.get("currentMember", False),
                        "start_date": None,
                        "url": member.get("url"),
                    }

            except requests.RequestException as e:
                logger.error(f"Failed to fetch member {bioguide_id} from Congress.gov: {e}")

        return None

    def search_members(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for members by name.

        Args:
            query: Search query (partial name match)

        Returns:
            List of matching members
        """
        all_members = self.get_current_members()

        query_lower = query.lower()
        matches = [
            m for m in all_members
            if query_lower in m.get("first_name", "").lower()
            or query_lower in m.get("last_name", "").lower()
            or query_lower in m.get("full_name", "").lower()
        ]

        return matches

    def _normalize_party(self, party: str) -> str:
        """Normalize party code to single letter."""
        if not party:
            return "O"

        party_lower = party.lower()

        if "democrat" in party_lower or party.upper() == "D":
            return "D"
        elif "republican" in party_lower or party.upper() == "R":
            return "R"
        elif "independent" in party_lower or party.upper() in ("I", "ID"):
            return "I"
        else:
            return "O"

    def _get_current_congress(self) -> int:
        """Get current Congress number based on year."""
        year = datetime.now().year
        # Congress number formula: (year - 1789) / 2 + 1
        # 2025-2026 = 119th Congress
        return ((year - 1789) // 2) + 1


# Backwards compatibility alias for code that imports ProPublicaClient
ProPublicaClient = CongressGovClient

