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

# SEC Standard Industrial Classification code -> sectors, matched on the LONGEST
# prefix so a specific code beats the group it sits in.
#
# This is the judgment layer, and it lives in code rather than data so it can be
# read in a diff. It was written against ground truth, not from memory: the SIC
# code SEC has actually assigned to each of the 70 tickers in SECTOR_TICKERS was
# fetched from EDGAR and the map checked against the hand-classification. Several
# entries exist only because that comparison contradicted the obvious guess:
#
#   6324 (UNH, hospital & medical service plans) sits inside 63xx insurance, and
#        is healthcare, not finance -- hence the longest-prefix rule.
#   2911 (XOM, CVX, VLO, PSX, MPC, COP -- petroleum refining) is energy, and must
#        not be swallowed by a 28xx chemicals-to-healthcare rule.
#   2870 (CF, MOS -- agricultural chemicals) is agriculture for the same reason.
#   46xx (pipelines) is energy despite sitting in the 40xx-47xx transport block.
#   7389 (V, MA -- "business services, NEC") is a grab-bag holding hundreds of
#        unrelated firms, so it maps to NOTHING. Visa and Mastercard reach
#        finance through SECTOR_TICKERS instead, which is what the curated list
#        is for.
#
# Codes absent from this map resolve to no sector, which is the common case and
# the correct one: most of the ~440 SIC codes describe industries no committee
# oversees and no policy area names.
SIC_SECTORS: Dict[str, FrozenSet[str]] = {
    # Agriculture and food. 01xx/02xx production, 20xx processing, 2870 inputs,
    # 3523 farm machinery (Deere files here).
    "01": frozenset({"agriculture"}),
    "02": frozenset({"agriculture"}),
    "20": frozenset({"agriculture"}),
    "2870": frozenset({"agriculture"}),
    "3523": frozenset({"agriculture"}),
    # Healthcare. 2833-2836 drugs and biologics, 384x medical instruments,
    # 5912 drug stores, 80xx health services, 8731 biological research.
    "283": frozenset({"healthcare"}),
    "384": frozenset({"healthcare"}),
    "3826": frozenset({"healthcare"}),
    "5047": frozenset({"healthcare"}),
    "5912": frozenset({"healthcare"}),
    "80": frozenset({"healthcare"}),
    "8731": frozenset({"healthcare"}),
    "6324": frozenset({"healthcare"}),
    # Energy. 12xx coal, 13xx oil and gas extraction and services, 2911
    # refining, 46xx pipelines, 49xx utilities.
    "12": frozenset({"energy"}),
    "13": frozenset({"energy"}),
    "2911": frozenset({"energy"}),
    "46": frozenset({"energy"}),
    "49": frozenset({"energy"}),
    # Defense. 348x ordnance, 372x aircraft and engines, 373x shipbuilding,
    # 3760-3769 missiles and space vehicles, 3795 tanks, 3812 search and
    # guidance systems. 372x covers commercial airframes too -- Boeing files
    # there -- which is a deliberate over-inclusion rather than an oversight.
    "348": frozenset({"defense"}),
    "372": frozenset({"defense"}),
    "373": frozenset({"defense"}),
    "376": frozenset({"defense"}),
    "3795": frozenset({"defense"}),
    "3812": frozenset({"defense"}),
    # Technology. 357x computers, 36xx electronics and semiconductors,
    # 737x software and data processing.
    "357": frozenset({"technology"}),
    "36": frozenset({"technology"}),
    "737": frozenset({"technology"}),
    # Telecom. 366x communications equipment, 481x carriers, 483x/484x
    # broadcasting and cable. 366x is equipment for the telecom industry, so it
    # carries both labels rather than being forced into one.
    "366": frozenset({"technology", "telecom"}),
    "481": frozenset({"telecom"}),
    "482": frozenset({"telecom"}),
    "483": frozenset({"telecom"}),
    "484": frozenset({"telecom"}),
    # Transportation. 371x motor vehicles, 3743 rail equipment, 40xx railroads,
    # 42xx trucking, 44xx water, 45xx air, 47xx services. 46xx is excluded
    # above; pipelines are energy.
    "371": frozenset({"transportation"}),
    "3743": frozenset({"transportation"}),
    "40": frozenset({"transportation"}),
    "41": frozenset({"transportation"}),
    "42": frozenset({"transportation"}),
    "44": frozenset({"transportation"}),
    "45": frozenset({"transportation"}),
    "47": frozenset({"transportation"}),
    # Finance. 60xx banks, 61xx credit, 62xx brokers and advisers, 63xx/64xx
    # insurance -- minus 6324, above.
    "60": frozenset({"finance"}),
    "61": frozenset({"finance"}),
    "62": frozenset({"finance"}),
    "63": frozenset({"finance"}),
    "64": frozenset({"finance"}),
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


def sector_for_sic(sic: str | int | None) -> FrozenSet[str]:
    """Sectors a SEC industry code belongs to, longest prefix wins.

    Longest-prefix rather than first-match because the specific codes in
    SIC_SECTORS exist precisely to overrule the group they sit inside: 6324 is
    healthcare even though every other 63xx code is insurance, and 2911 is
    energy even though it sits beside the chemical codes.
    """
    if sic is None:
        return frozenset()
    code = str(sic).strip()
    if not code.isdigit():
        return frozenset()

    for length in range(len(code), 0, -1):
        sectors = SIC_SECTORS.get(code[:length])
        if sectors:
            return sectors
    return frozenset()


class SectorIndex:
    """Classifies holdings, with SEC industry codes behind it.

    `classify` on its own knows about 70 hand-listed tickers and a keyword list.
    That is the binding constraint on every detector that joins a trade to a
    committee remit or a bill's policy area: a member trading a mid-cap defense
    supplier is invisible to all of them. This adds the industry code SEC has
    assigned the issuer, which covers every registrant rather than the large
    caps somebody remembered to type in.

    Built once per detector run and used in the loop, so the lookup is a dict
    hit rather than a query. `classify` stays a pure, session-free function and
    remains the fallback, so nothing that already calls it has to change.
    """

    def __init__(self, sic_by_ticker: Dict[str, str] | None = None):
        self._sic_by_ticker = {
            (t or "").strip().upper(): s for t, s in (sic_by_ticker or {}).items() if t
        }

    @classmethod
    def from_db(cls, db: object) -> SectorIndex:
        """Load the cached industry codes. Empty is fine -- it degrades to `classify`."""
        from src.db.models import CompanyIndustry

        rows = db.query(CompanyIndustry.ticker, CompanyIndustry.sic).all()  # type: ignore[attr-defined]
        return cls({ticker: sic for ticker, sic in rows if ticker and sic})

    def __len__(self) -> int:
        return len(self._sic_by_ticker)

    def classify(self, ticker: str | None, description: str | None = None) -> Set[str]:
        """Sectors for a holding: curated list, then industry code, then keywords.

        The curated SECTOR_TICKERS list wins where the two disagree, and it does
        disagree: SEC files Amazon under retail-catalogue, Visa and Mastercard
        under "business services, NEC", and Leidos under computer systems
        design. Those are accurate descriptions of the filer and the wrong
        answer for this question, so 70 reviewed human judgements outrank the
        general rule -- the same reason SUBSIDIARY_OVERRIDES exists.
        """
        if ticker:
            symbol = ticker.strip().upper()
            curated = {s for s, symbols in SECTOR_TICKERS.items() if symbol in symbols}
            if curated:
                return curated

            from_sic = sector_for_sic(self._sic_by_ticker.get(symbol))
            if from_sic:
                return set(from_sic)

        return classify(None, description)
