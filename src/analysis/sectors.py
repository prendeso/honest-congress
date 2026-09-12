"""One sector taxonomy, and how committees map onto it.

This replaces four separate keyword tables that had drifted apart: two in
`trade_analyzer`/`extended_anomaly_detector` and two more in the deleted
`committee_conflict_mapper`. They disagreed on sector names ("pharma" vs
"healthcare", "tech" vs "technology") while being consumed as if they matched.

More importantly, they mixed company names and bare stock tickers into one list
and matched every entry as a substring. With ``"ba"`` in the defense list,
"Alibaba" was a defense company; ``"gs"`` matched anything containing those
letters. Tickers are matched exactly here, and names on word boundaries.

Committee jurisdiction is keyed on thomas_id from congress-legislators. It is
deliberately coarse -- a claim that a committee's remit touches a sector, not
that any particular trade was influenced by it.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, Set

# Sector -> exact ticker symbols. Matched case-insensitively against
# Transaction.ticker only, never against free text.
SECTOR_TICKERS: Dict[str, FrozenSet[str]] = {
    "defense": frozenset({"LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII", "LDOS"}),
    "technology": frozenset(
        {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "AMD", "INTC", "CRM", "ORCL"}
    ),
    "healthcare": frozenset(
        {"PFE", "MRNA", "MRK", "JNJ", "ABBV", "LLY", "UNH", "CVS", "BMY", "AMGN"}
    ),
    "finance": frozenset({"JPM", "WFC", "GS", "MS", "BAC", "C", "V", "MA", "AXP", "SCHW"}),
    "energy": frozenset({"XOM", "CVX", "COP", "SLB", "OXY", "PSX", "VLO", "MPC"}),
    "telecom": frozenset({"T", "VZ", "TMUS", "CMCSA", "CHTR"}),
    "transportation": frozenset({"UNP", "CSX", "NSC", "DAL", "UAL", "LUV", "UPS", "FDX"}),
    "agriculture": frozenset({"ADM", "BG", "CTVA", "DE", "MOS", "CF"}),
}

# Sector -> distinctive words appearing in an asset description. Matched on word
# boundaries, so "bank" does not match "Eurobank" and short tokens cannot match
# inside longer words.
SECTOR_KEYWORDS: Dict[str, FrozenSet[str]] = {
    "defense": frozenset({"defense", "lockheed", "raytheon", "northrop", "boeing", "aerospace"}),
    "technology": frozenset(
        {"software", "semiconductor", "microsoft", "apple", "nvidia", "alphabet", "intel"}
    ),
    "healthcare": frozenset(
        {"pharmaceutical", "pharma", "biotech", "health", "medical", "pfizer", "moderna", "merck"}
    ),
    "finance": frozenset(
        {"bank", "bancorp", "financial", "insurance", "jpmorgan", "goldman", "citigroup"}
    ),
    "energy": frozenset({"energy", "petroleum", "exxon", "chevron", "conoco", "pipeline"}),
    "telecom": frozenset({"telecom", "wireless", "broadband", "verizon", "comcast"}),
    "transportation": frozenset({"airlines", "railroad", "railway", "logistics", "freight"}),
    "agriculture": frozenset({"agriculture", "agricultural", "farm", "fertilizer", "deere"}),
}

# Committee thomas_id -> sectors within its remit. Sourced from the committees'
# published jurisdictions. Coarse by design.
COMMITTEE_SECTOR_JURISDICTION: Dict[str, FrozenSet[str]] = {
    # House
    "HSAS": frozenset({"defense"}),  # Armed Services
    "HSBA": frozenset({"finance"}),  # Financial Services
    "HSIF": frozenset({"energy", "healthcare", "telecom"}),  # Energy and Commerce
    "HSAG": frozenset({"agriculture"}),  # Agriculture
    "HSPW": frozenset({"transportation"}),  # Transportation and Infrastructure
    "HSSY": frozenset({"technology"}),  # Science, Space, and Technology
    "HSWM": frozenset({"finance", "healthcare"}),  # Ways and Means
    "HSII": frozenset({"energy"}),  # Natural Resources
    # Senate
    "SSAS": frozenset({"defense"}),  # Armed Services
    "SSBK": frozenset({"finance"}),  # Banking, Housing, and Urban Affairs
    "SSCM": frozenset({"telecom", "transportation", "technology"}),  # Commerce, Science
    "SSEG": frozenset({"energy"}),  # Energy and Natural Resources
    "SSAF": frozenset({"agriculture"}),  # Agriculture, Nutrition, and Forestry
    "SSHR": frozenset({"healthcare"}),  # Health, Education, Labor, and Pensions
    "SSFI": frozenset({"finance", "healthcare"}),  # Finance
}

# CRS policy area -> sectors. Congress.gov assigns each bill exactly one policy
# area from a fixed vocabulary; sampling 1,770 real bills returned 34 distinct
# values, and these are the seven that identify an industry rather than a theme.
#
# The omissions are the point. "Taxation", "Commerce", "Environmental
# Protection" and "Economics and Public Finance" all appear frequently and all
# touch every issuer in the market, so routing them to a sector would let the
# detector claim a specific company link it cannot support. They stay unmapped
# deliberately, and a test pins that so a later edit cannot quietly widen the
# claim.
#
# Coverage, measured on 9,896 real bills: about 35% fall in one of these seven.
# That ceiling is real and the ingester reports it.
POLICY_AREA_SECTORS: Dict[str, FrozenSet[str]] = {
    "Armed Forces and National Security": frozenset({"defense"}),
    "Health": frozenset({"healthcare"}),
    "Energy": frozenset({"energy"}),
    "Finance and Financial Sector": frozenset({"finance"}),
    "Science, Technology, Communications": frozenset({"technology", "telecom"}),
    "Transportation and Public Works": frozenset({"transportation"}),
    "Agriculture and Food": frozenset({"agriculture"}),
}


_WORD_PATTERNS: Dict[str, re.Pattern[str]] = {
    sector: re.compile(r"\b(?:" + "|".join(sorted(words)) + r")\b", re.IGNORECASE)
    for sector, words in SECTOR_KEYWORDS.items()
    if words
}

ALL_SECTORS: FrozenSet[str] = frozenset(SECTOR_TICKERS) | frozenset(SECTOR_KEYWORDS)


def classify(ticker: str | None, description: str | None = None) -> Set[str]:
    """Return the sectors a holding belongs to.

    An exact ticker match is authoritative and returned alone. Only when the
    ticker is absent or unrecognised does the description get consulted, since
    keyword matching over free text is far weaker evidence.
    """
    if ticker:
        symbol = ticker.strip().upper()
        matched = {sector for sector, symbols in SECTOR_TICKERS.items() if symbol in symbols}
        if matched:
            return matched

    if not description:
        return set()

    return {sector for sector, pattern in _WORD_PATTERNS.items() if pattern.search(description)}


def committee_sectors(committee_id: str | None) -> FrozenSet[str]:
    """Sectors within a committee's remit, following subcommittees to the parent.

    Subcommittee ids are the parent id plus a numeric suffix, and a
    subcommittee sits inside its parent's jurisdiction.
    """
    if not committee_id:
        return frozenset()

    if committee_id in COMMITTEE_SECTOR_JURISDICTION:
        return COMMITTEE_SECTOR_JURISDICTION[committee_id]

    for parent_id, sectors in COMMITTEE_SECTOR_JURISDICTION.items():
        if committee_id.startswith(parent_id):
            return sectors

    return frozenset()


def policy_area_sectors(policy_area: str | None) -> FrozenSet[str]:
    """Sectors a bill's CRS policy area identifies, or empty.

    Empty is the common case and not a failure: most policy areas name a theme
    ("Government Operations and Politics") rather than an industry, and a bill
    that has not been classified yet has no policy area at all.
    """
    if not policy_area:
        return frozenset()
    return POLICY_AREA_SECTORS.get(policy_area.strip(), frozenset())
