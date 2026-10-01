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
