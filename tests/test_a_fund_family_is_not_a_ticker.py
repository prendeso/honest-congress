""" "ProShares TR" is a fund family and a trust, not Tootsie Roll.

The keyword fallback in `_extract_ticker` looked for "shares" as a substring,
found it inside "ProShares", and took the first run of capitals as the symbol.
Peter Welch's "ProShares TR UltraPro Short S&P 500" -- a 3x inverse S&P 500
fund -- was stored as TR and published as trading in agriculture.
"""

from __future__ import annotations

import pytest

from src.parsing.ptr_parser import PTRParser


@pytest.mark.parametrize(
    "description",
    [
        "ProShares TR UltraPro Short S&P 500",
        "ProShares TR Ultra QQQ",
        "SPDR S&P 500 ETF TR shares",
    ],
)
def test_a_fund_name_yields_no_ticker(description):
    assert PTRParser()._extract_ticker(description) is None


def test_an_explicit_tr_is_still_tootsie_roll():
    assert PTRParser()._extract_ticker("Tootsie Roll Industries (TR) Common Stock") == "TR"


@pytest.mark.parametrize(
    ("description", "ticker"),
    [
        ("Apple Inc. Common Stock AAPL", "AAPL"),
        ("NVIDIA Corp shares NVDA", "NVDA"),
    ],
)
def test_the_keyword_fallback_still_works(description, ticker):
    assert PTRParser()._extract_ticker(description) == ticker


@pytest.mark.parametrize(
    "description",
    [
        "FIDELITY MID CAP STOCK",
        "ISHARES CORE S&P 500 ETF SHARES",
        "MICROSOFT CORP COMMON STOCK",
        "Iberdrola SA Bilbao Ordinary Shares",
        "iShares Trust CORE Dividend Growth shares",
    ],
)
def test_words_in_capitals_are_not_symbols(description):
    """All read as tickers in production: MID, CORE, STOCK, SA."""
    assert PTRParser()._extract_ticker(description) is None


@pytest.mark.parametrize(
    ("description", "ticker"),
    [("Colgate-Palmolive Company (CL)", "CL"), ("BILL Holdings, Inc. Common Stock (BILL)", "BILL")],
)
def test_an_explicit_symbol_that_is_also_a_word_is_kept(description, ticker):
    assert PTRParser()._extract_ticker(description) == ticker
