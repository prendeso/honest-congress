"""Resolve company names to stock tickers, from SEC's official register.

Every Tier-2 table keys on `ticker`, but the official sources publish names:
FEC gives PAC names, the Senate LDA gives registrants and clients, USASpending
gives recipient names. QuiverQuant did this resolution for us; this is how it
gets done without a vendor.

SEC publishes `company_tickers.json` -- roughly 10,400 CIK/ticker/name triples,
public domain, no key. SEC's access policy requires a declared User-Agent, and
requests without one are refused with a 403.

**Coverage is the point, not a shortfall.** Measured against real 2024 data:
**70% of federal contract dollars** (the 300 largest award actions of 2024) and
**41% of corporate PACs** (all 1,675 active in the cycle) resolve to a ticker.

The misses are overwhelmingly entities that are not publicly traded at all --
the national-laboratory management LLCs, universities, trade associations,
mutual insurers, and private firms like Bechtel and TriWest. A
conflict-of-interest detector keyed on tradeable securities *should* skip those,
and the loudest of them are recorded in SUBSIDIARY_OVERRIDES with an empty
ticker so nobody later mistakes them for a gap and guesses.

What it must not skip is a listed parent hiding behind a subsidiary name. That
is the rest of SUBSIDIARY_OVERRIDES, and it is worth real money: Electric Boat
is General Dynamics ($2.6bn in one 2024 action), Optum Public Sector Solutions
is UnitedHealth (33 separate actions), Health Net Federal Services is Centene.
Adding those thirteen entries moved contract-dollar coverage from 56% to 70%.
"""

from __future__ import annotations

import logging
import re
from typing import Dict

import requests

logger = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


# SEC refuses requests that do not identify the caller, and the identification
# must include a contact EMAIL -- a descriptive string alone returns 403.
# Verified: "honest-congress (congressional disclosure research)" -> 403,
# "honest-congress contact@example.com" -> 200.
# See https://www.sec.gov/os/webmaster-faq#developers
def _user_agent() -> str:
    from src.config import get_settings

    return f"honest-congress {get_settings().sec_contact_email}"


REQUEST_TIMEOUT = 60

# Corporate and legal-form words that carry no identifying signal. Stripped from
# both sides before comparison so "Lockheed Martin Corp" and "LOCKHEED MARTIN
# CORPORATION" collapse to the same key.
_NOISE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|lp|llp|ltd|"
    r"limited|plc|holdings?|group|the|and|of)\b",
    re.IGNORECASE,
)

# Additional words that appear only in political-committee names.
_PAC_NOISE = re.compile(
    r"\b(pac|political|action|committee|employees?|federal|fund|inc\.?)\b",
    re.IGNORECASE,
)

_PUNCT = re.compile(r"[^a-z0-9 ]+")

# SEC titles carry the state or country of incorporation as a slashed suffix --
# "NORTHROP GRUMMAN CORP /DE/", "PROGRESSIVE CORP/OH/", "BANK OF MONTREAL /CAN/".
# 287 of the 10,400 registered titles do. Stripped before punctuation, because
# afterwards "/OH/" is just the word "oh" and survives every other filter. Left
# in, it defeats the prefix match: "Northrop Grumman Systems Corp" on a federal
# contract never reaches "northrop grumman de".
_STATE_MARKER = re.compile(r"/[A-Za-z][A-Za-z ]{0,18}/")

# Wholly-owned subsidiaries that file under their own name but whose parent is
# the tradeable entity. Without these the detector misses, for example, a
# $34bn Electric Boat award that belongs to General Dynamics.
SUBSIDIARY_OVERRIDES: Dict[str, str] = {
    "electric boat": "GD",
    "general dynamics electric boat": "GD",
    "bath iron works": "GD",
    "gulfstream aerospace": "GD",
    "sikorsky aircraft": "LMT",
    "pratt whitney": "RTX",
    "collins aerospace": "RTX",
    "raytheon": "RTX",
    "mcdonnell douglas": "BA",
    "jeppesen": "BA",
    "humana government business": "HUM",
    "united launch alliance": "BA",
    "optum": "UNH",
    "health net federal services": "CNC",
    "honeywell federal manufacturing": "HON",
    # Privately held, recorded so nobody adds a guess later. These are the
    # largest recurring misses in the 2024 contract data, and every one of them
    # is correctly a miss -- there is no security to trade.
    "blue origin": "",
    "triwest healthcare": "",
    "bechtel": "",
    "consolidated nuclear security": "",
    "lawrence livermore national security": "",
    "savannah river nuclear solutions": "",
    "triad national security": "",
    "ut battelle": "",
    "national technology engineering solutions": "",
}


def _normalize(name: str, *, political: bool = False) -> str:
    """Reduce a company name to a comparable key."""
    text = _STATE_MARKER.sub(" ", name or "")
    text = _PUNCT.sub(" ", text.lower())
    if political:
        text = _PAC_NOISE.sub(" ", text)
    text = _NOISE.sub(" ", text)
    return " ".join(text.split())


class TickerResolver:
    """Name-to-ticker lookup backed by SEC's company register."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self._index: Dict[str, str] = {}
        self._names: Dict[str, str] = {}
        self._loaded = False

    def load(self) -> int:
        """Fetch and index the SEC register. Returns the number of companies."""
        response = self.session.get(
            SEC_TICKERS_URL,
            headers={"User-Agent": _user_agent()},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        index: Dict[str, str] = {}
        names: Dict[str, str] = {}
        for entry in payload.values():
            ticker = (entry.get("ticker") or "").strip().upper()
            # The incorporation marker is stripped here too, not just in the
            # comparison key: `name_for` feeds the LDA client-name query, and
            # "NORTHROP GRUMMAN CORP /DE/" matches no lobbying client.
            title = " ".join(_STATE_MARKER.sub(" ", entry.get("title") or "").split())
            key = _normalize(title)
            if ticker and key:
                # First writer wins: the register is ordered by market cap, so
                # the larger issuer keeps an ambiguous name.
                index.setdefault(key, ticker)
                names.setdefault(ticker, title)

        self._index = index
        self._names = names
        self._loaded = True
        logger.info("Indexed %d companies from the SEC register", len(index))
        return len(index)

    def resolve(self, name: str, *, political: bool = False) -> str | None:
        """Best-effort ticker for a company name, or None.

        `political` also strips committee boilerplate, for FEC PAC names like
        "EMPLOYEES OF NORTHROP GRUMMAN CORPORATION PAC". Measured over all 1,675
        corporate PACs active in the 2024 cycle it resolves 19 that the plain
        pass misses, and picks the better of two candidates three more times
        ("COCA-COLA CONSOLIDATED" is the bottler COKE, not KO; "NU SKIN
        ENTERPRISES" is NUS, not NuScale Power).

        It is tried *first* rather than *instead*, because the boilerplate words
        also occur inside real company names: stripping "federal" from "FEDERAL
        AGRICULTURAL MORTGAGE CORPORATION" loses AGM outright. Falling back to
        the unstripped key costs one dictionary lookup and gets it back.
        """
        if not self._loaded:
            self.load()

        keys = []
        if political:
            keys.append(_normalize(name, political=True))
        keys.append(_normalize(name))

        for key in keys:
            if not key:
                continue
            ticker = self._lookup(key)
            if ticker is not None:
                return ticker or None

        return None

    def _lookup(self, key: str) -> str | None:
        """Resolve one normalized key. Returns "" for a known-private match.

        The empty string and None are different answers: "" means an override
        matched and deliberately declares the company untradeable, so the caller
        must stop rather than fall through to a looser pass.
        """
        for fragment, ticker in SUBSIDIARY_OVERRIDES.items():
            if fragment in key:
                return ticker

        if key in self._index:
            return self._index[key]

        # A registered name is often the filing name plus a qualifier, or the
        # other way round: "Archer Aviation" vs "Archer Aviation Inc.". Anchored
        # on a space so this stays a word-boundary rule -- an unanchored
        # substring test would resolve "Catering Services" to Caterpillar.
        for indexed, ticker in self._index.items():
            if key.startswith(f"{indexed} ") or indexed.startswith(f"{key} "):
                return ticker

        return None

    def name_for(self, ticker: str) -> str | None:
        """The registered company name for a ticker, or None.

        The reverse of :meth:`resolve`, and the reason the LDA ingester is
        affordable: the Senate lobbying API has no ticker filter and published
        96,941 filings for 2024 alone, so the only tractable query is by client
        name -- which means starting from the tickers members actually traded
        and asking the register what those companies are called.
        """
        if not self._loaded:
            self.load()
        return self._names.get((ticker or "").strip().upper())
