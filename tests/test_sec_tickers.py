"""Name-to-ticker resolution, pinned to a slice of the real SEC register.

`tests/fixtures/sec/company_tickers.json` is thirteen entries copied verbatim
out of https://www.sec.gov/files/company_tickers.json, chosen because each one
exercises a case that cost real debugging time:

* SEC refuses a User-Agent without a contact email. Verified live:
  "honest-congress (congressional disclosure research)" -> 403,
  "honest-congress contact@example.com" -> 200. The header is load-bearing, so
  a test asserts it carries an address.
* 287 registered titles carry the state of incorporation as a slashed suffix
  ("NORTHROP GRUMMAN CORP /DE/", "PROGRESSIVE CORP/OH/"). Left in, the federal
  contract recipient "NORTHROP GRUMMAN SYSTEMS CORP" resolves to nothing.
* Wholly-owned subsidiaries file under their own name. The largest single 2024
  contract action in the fixture belongs to "ELECTRIC BOAT CORPORATION", which
  is General Dynamics -- $2.6bn that a ticker-keyed detector would otherwise
  never see.
* Some entries in the register have no ticker at all, and must not be indexed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.ingestion.sec_tickers import SEC_TICKERS_URL, TickerResolver, _normalize

FIXTURE = Path(__file__).parent / "fixtures" / "sec" / "company_tickers.json"


@pytest.fixture
def resolver() -> TickerResolver:
    payload = json.loads(FIXTURE.read_text())
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    session.get.return_value = response
    return TickerResolver(session=session)


def test_load_indexes_only_entries_with_a_ticker(resolver):
    indexed = resolver.load()
    payload = json.loads(FIXTURE.read_text())
    with_ticker = [e for e in payload.values() if e["ticker"]]
    assert indexed == len(with_ticker)
    assert indexed == len(payload) - 1  # the "NO TICKER HOLDING CO" row


def test_request_identifies_the_caller_with_an_email(resolver):
    resolver.load()
    _, kwargs = resolver.session.get.call_args
    assert resolver.session.get.call_args[0][0] == SEC_TICKERS_URL
    assert "@" in kwargs["headers"]["User-Agent"]


def test_load_happens_once_lazily(resolver):
    assert resolver.resolve("Apple Inc.") == "AAPL"
    assert resolver.resolve("MICROSOFT CORP") == "MSFT"
    assert resolver.session.get.call_count == 1


@pytest.mark.parametrize(
    "name,expected",
    [
        # Legal form and case are noise.
        ("LOCKHEED MARTIN CORPORATION", "LMT"),
        ("Lockheed Martin Corp", "LMT"),
        ("lockheed martin", "LMT"),
        ("The Boeing Company", "BA"),
        # Incorporation marker stripped from the registered title.
        ("NORTHROP GRUMMAN CORP", "NOC"),
        # ... which is what lets the operating subsidiary through.
        ("NORTHROP GRUMMAN SYSTEMS CORP", "NOC"),
        # Registered name is the filing name plus a qualifier, or vice versa.
        ("Archer Aviation", "ACHR"),
        ("Archer Aviation Inc.", "ACHR"),
    ],
)
def test_resolves_real_recipient_names(resolver, name, expected):
    assert resolver.resolve(name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ELECTRIC BOAT CORPORATION", "GD"),
        ("GENERAL DYNAMICS ELECTRIC BOAT", "GD"),
        ("BATH IRON WORKS CORPORATION", "GD"),
        ("HUMANA GOVERNMENT BUSINESS INC", "HUM"),
        ("Sikorsky Aircraft Corp", "LMT"),
        ("Pratt & Whitney", "RTX"),
    ],
)
def test_subsidiaries_resolve_to_the_listed_parent(resolver, name, expected):
    assert resolver.resolve(name) == expected


def test_privately_held_override_resolves_to_none_not_a_phantom_ticker(resolver):
    # "blue origin" maps to "" in SUBSIDIARY_OVERRIDES -- recorded so nobody
    # adds a guess later, but it must never surface as a ticker.
    assert resolver.resolve("Blue Origin LLC") is None


@pytest.mark.parametrize(
    "name",
    [
        "TRIAD NATIONAL SECURITY, LLC",
        "SAVANNAH RIVER NUCLEAR SOLUTIONS LLC",
        "UT-BATTELLE LLC",
        "THE JOHNS HOPKINS UNIVERSITY",
        "",
        "   ",
    ],
)
def test_entities_that_are_not_publicly_traded_resolve_to_none(resolver, name):
    # Not a coverage gap: a conflict detector keyed on tradeable securities has
    # nothing to say about a national laboratory or a university.
    assert resolver.resolve(name) is None


def test_political_mode_strips_committee_boilerplate(resolver):
    # The plain pass cannot reach this one: the company name is not a prefix of
    # the PAC name, it is buried in the middle of it. Measured over the 1,675
    # corporate PACs active in the 2024 cycle, the flag resolves 19 more.
    assert resolver.resolve("EMPLOYEES OF NORTHROP GRUMMAN CORPORATION PAC") is None
    assert (
        resolver.resolve("EMPLOYEES OF NORTHROP GRUMMAN CORPORATION PAC", political=True) == "NOC"
    )


def test_political_mode_falls_back_when_stripping_destroys_the_name(resolver):
    # "federal" is PAC boilerplate and also the first word of a real issuer.
    # Stripping it unconditionally lost Federal Agricultural Mortgage (AGM)
    # from the live FEC list, which is why the stripped key is tried first
    # rather than instead.
    assert resolver.resolve("CATERPILLAR FEDERAL FUND PAC", political=True) == "CAT"
    assert resolver.resolve("Caterpillar Inc", political=True) == "CAT"


def test_political_mode_does_not_invent_matches(resolver):
    assert resolver.resolve("NATIONAL ASSOCIATION OF REALTORS PAC", political=True) is None


def test_private_override_is_not_undone_by_the_fallback_pass(resolver):
    # "" in SUBSIDIARY_OVERRIDES is a deliberate "not tradeable" answer, not a
    # miss -- the second pass must not treat it as one and keep searching.
    assert resolver.resolve("BLUE ORIGIN FEDERAL PAC", political=True) is None


def test_a_one_word_registered_name_does_not_claim_everything_after_it(resolver):
    # "UNIVERSAL CORP" indexes as "universal". Universal Synaptics is a
    # different company, and attributing its filings to a Virginia tobacco
    # issuer would put a wrong company name next to a real member's trades.
    assert resolver.resolve("UNIVERSAL CORPORATION") == "UVV"
    assert resolver.resolve("UNIVERSAL SYNAPTICS CORPORATION") is None
    assert resolver.resolve("CATERPILLAR DEALERS ASSOCIATION") is None


def test_a_two_word_prefix_still_reaches_the_operating_subsidiary(resolver):
    # This is the case the prefix rule exists for, and it survives the
    # strictness: two words is enough to identify, one is not.
    assert resolver.resolve("NORTHROP GRUMMAN SYSTEMS CORPORATION") == "NOC"


def test_pac_names_may_match_on_one_word(resolver):
    # A PAC name is the company name plus committee boilerplate by
    # construction, so the trailing words are noise rather than identity.
    # Requiring two words costs 187 of the 1,675 real corporate PACs.
    assert resolver.resolve("CATERPILLAR COMMITTEE FOR BETTER GOVERNMENT") is None
    assert resolver.resolve("CATERPILLAR COMMITTEE FOR BETTER GOVERNMENT", political=True) == "CAT"


def test_a_pac_alias_in_parentheses_is_dropped(resolver):
    # Real names repeat themselves: "AFLAC POLITICAL ACTION COMMITTEE
    # (AFLAC PAC)". Left in, the company name appears twice and matches
    # nothing; stripping it recovers 84 PACs on its own.
    assert resolver.resolve("CATERPILLAR INC. PAC (CATPAC)", political=True) == "CAT"
    assert resolver.resolve("3M COMPANY PAC (3M PAC)", political=True) == "MMM"


def test_the_longest_matching_registered_name_wins(resolver):
    # Both "boeing" and "caterpillar" are registered one-word names; a more
    # specific registered name must never be shadowed by a shorter one it
    # contains.
    assert resolver.resolve("LOCKHEED MARTIN CORPORATION SPACE SYSTEMS") == "LMT"


def test_prefix_match_respects_word_boundaries(resolver):
    # "CAT" is Caterpillar. A substring rule would have "CATERING SERVICES"
    # or "CATALYST PHARMA" resolve to it; the space-anchored prefix does not.
    assert resolver.resolve("Caterpillar Inc") == "CAT"
    assert resolver.resolve("Catalyst Pharmaceuticals") is None
    assert resolver.resolve("Catering Services") is None


def test_name_for_is_the_reverse_lookup(resolver):
    resolver.load()
    assert resolver.name_for("LMT") == "LOCKHEED MARTIN CORP"
    assert resolver.name_for("lmt") == "LOCKHEED MARTIN CORP"
    assert resolver.name_for("ZZZZ") is None


def test_name_for_drops_the_incorporation_marker(resolver):
    # It is sent verbatim to the LDA client-name query, where
    # "NORTHROP GRUMMAN CORP /DE/" matches no lobbying client.
    assert resolver.name_for("NOC") == "NORTHROP GRUMMAN CORP"


def test_normalize_collapses_legal_forms():
    assert _normalize("Lockheed Martin Corporation") == _normalize("LOCKHEED MARTIN CORP")
    assert _normalize("The TJX Companies, Inc.") == _normalize("TJX Companies")
    assert _normalize("PROGRESSIVE CORP/OH/") == "progressive"


def test_normalize_only_strips_the_alias_for_political_names():
    # A parenthetical is boilerplate on a PAC and can be real on a company
    # ("THE BOEING COMPANY (F.N.A. AURORA FLIGHT SCIENCES)" is still Boeing,
    # but the filing name is what the LDA matched on).
    assert _normalize("AFLAC PAC (AFLAC POLITICAL ACTION COMMITTEE)", political=True) == "aflac"
    assert "aurora" in _normalize("BOEING (F.N.A. AURORA FLIGHT SCIENCES)")
