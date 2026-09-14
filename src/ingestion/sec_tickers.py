"""Resolve company names to stock tickers, from SEC's official register.

Every Tier-2 table keys on `ticker`, but the official sources publish names:
FEC gives PAC names, the Senate LDA gives registrants and clients, USASpending
gives recipient names. The vendor this project used to depend on did that
resolution for us; this is how it gets done from public sources instead.

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
Writing those down moved contract-dollar coverage from 56% to 70%.

They are written down rather than inferred on purpose. An earlier version let a
single registered word claim anything starting with it, which did catch these --
and also gave Fermi Research Alliance (a DOE laboratory consortium) to an
Estonian nuclear startup and Universal Synaptics to a Virginia tobacco company.
For a project whose findings name real people, a silent misattribution is worse
than a silent miss, so the loose rule went and the true positives became
explicit, reviewable entries.
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

# PAC names carry their own abbreviation as a trailing parenthetical --
# "AFLAC POLITICAL ACTION COMMITTEE (AFLAC PAC)", "ADEIA INC POLITICAL ACTION
# COMMITTEE (ADEIA PAC)". Left in, the company name appears twice and matches
# nothing. Stripping it recovers 84 of the 1,675 corporate PACs on its own.
_TRAILING_ALIAS = re.compile(r"\s*\([^()]*\)\s*$")

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
    # Operating subsidiaries of listed parents, each verified against a real
    # 2024 federal award. These used to be caught by a one-word prefix, which
    # also caught Fermi Research Alliance and Universal Synaptics -- so the loose
    # rule went and the true positives were written down instead.
    "amentum": "AMTM",
    "fluor marine propulsion": "FLR",
    "kbr services": "KBR",
    "leidos": "LDOS",
    "maximus federal": "MMS",
    "olin winchester": "OLN",
    "oracle health": "ORCL",
    "textron": "TXT",
    "v2x": "VVX",
    "health net federal services": "CNC",
    "honeywell federal manufacturing": "HON",
    # The federal-division names, found when the contract feed stopped taking a
    # global top-300 slice and started asking per traded company. Each of these
    # was rejected on every one of the hundred awards measured for its ticker,
    # so the company kept nothing at all: CACI, KBR, Dell, Chevron, Oracle and
    # Merck each scored zero.
    #
    # Every entry here is a claim about a NAME, not about corporate ownership.
    # The registrant's own name is the leading word or words and what follows is
    # a division: "KBR, INC." and "KBR WYLE SERVICES, LLC"; "CHEVRON CORP" and
    # "CHEVRON USA INC."; "ORACLE CORP" and "ORACLE AMERICA, INC"; "Merck & Co."
    # and "MERCK SHARP & DOHME LLC". Dell and CACI are the same shape one step
    # removed -- the registered name carries a descriptor the federal entity
    # drops ("Dell Technologies", "CACI International") -- which is still legible
    # in the string rather than a fact about who owns whom.
    #
    # The fragments are checked against all 8,007 registered names, because the
    # match here is an unanchored substring. A bare "caci" is NOT usable and is
    # why these two are spelled out: "acacia research" contains it, and ACTG
    # would have been handed every CACI award.
    "caci federal": "CACI",
    "caci nss": "CACI",
    "kbr wyle": "KBR",
    "dell federal systems": "DELL",
    "dell marketing": "DELL",
    "chevron usa": "CVX",
    "oracle america": "ORCL",
    "merck sharp": "MRK",
    # Deliberately NOT asserted, though USASpending's hierarchy offers them:
    # QTC Medical Services, Cepheid, Life Technologies, Thermo Electron,
    # National Instruments, Meridian Medical Technologies, Valor Healthcare,
    # Magellan Federal, Foundation Care, Ortho-Clinical Diagnostics. Each may
    # well belong to a listed parent, but nothing in the name says so and the
    # SEC register does not carry parentage -- and at least one of them is a
    # trap: Ortho-Clinical was Johnson & Johnson's until 2014 and is not now,
    # so the hierarchy that offers it is stale. `rejected_wrong_company` in
    # src/ingestion/usaspending.py counts what this costs on every run.
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
    if political:
        previous = None
        while previous != text:
            previous = text
            text = _TRAILING_ALIAS.sub("", text).strip()
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
        self._ciks: Dict[str, int] = {}
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
        ciks: Dict[str, int] = {}
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
                try:
                    ciks.setdefault(ticker, int(entry["cik_str"]))
                except (KeyError, TypeError, ValueError):
                    pass

        self._index = index
        self._names = names
        self._ciks = ciks
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
            ticker = self._lookup(key, min_prefix_words=1 if political else 2)
            if ticker is not None:
                return ticker or None

        return None

    def _lookup(self, key: str, *, min_prefix_words: int = 2) -> str | None:
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
        #
        # How long a prefix has to be before it counts as identification rather
        # than coincidence depends on what is being matched, and the difference
        # is structural rather than a tuning knob.
        #
        # A *company* name is the whole identity, so extra words mean a
        # different company: "UNIVERSAL CORP" indexes as "universal", and a
        # one-word rule hands Universal Synaptics and Fermi Research Alliance to
        # unrelated issuers. Two words is the line -- which still keeps
        # "northrop grumman" -> Northrop Grumman Systems, the case this rule
        # exists for. Operating subsidiaries that a one-word prefix would have
        # caught are listed in SUBSIDIARY_OVERRIDES instead, where each is a
        # reviewable assertion rather than a guess.
        #
        # A *PAC* name is the company name plus committee boilerplate by
        # construction, so the trailing words are noise and one word is enough.
        # Requiring two costs 187 of 1,675 corporate PACs -- Aflac, Airbnb,
        # Altria, Broadcom -- for no correctness gain.
        #
        # The longest match wins rather than the first, so a more specific
        # registered name is never shadowed by a shorter one it contains.
        best: str | None = None
        best_length = 0
        for indexed, ticker in self._index.items():
            length = len(indexed)
            if length <= best_length:
                continue
            if min_prefix_words <= indexed.count(" ") + 1 and key.startswith(f"{indexed} "):
                best, best_length = ticker, length
            elif min_prefix_words <= key.count(" ") + 1 and indexed.startswith(f"{key} "):
                best, best_length = ticker, length

        return best

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

    def ciks(self) -> Dict[str, int]:
        """Ticker -> CIK, from the same register the name index is built from.

        The CIK is what EDGAR's company endpoints are keyed on, so the industry
        ingester needs it; the register already carries it and was discarding it.
        """
        if not self._loaded:
            self.load()
        return dict(self._ciks)
