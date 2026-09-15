"""A bank account was stored as a Boeing holding.

On the House Clerk's annual disclosure form the two bracket styles mean
different things:

    Lazard International Strategic Equity Ptf Insti Shs (LISIX) [MF]
                                                        ^^^^^   ^^
                                                        ticker  class code

Parentheses hold the security's symbol. Square brackets hold the form's own
asset-class code — `[BA]` bank account, `[MF]` mutual fund, `[ST]` stock, `[RP]`
real property — which is never a ticker, and whose meaning the parser already
reads separately in `_determine_asset_type`.

`_extract_ticker` accepted either bracket, so it manufactured a symbol for every
asset that had none to give. The codes collide with real, heavily traded ones:
**BA is Boeing**, GS is Goldman Sachs, WU is Western Union.

Measured over 40 randomly sampled type-O House annual filings, by which bracket
the symbol came from:

    (parentheses)   514   XLY, IEFA, VDC, VGT, FCTDX, ITOT, SWVXX ...
    [square]        122   BA 50, CS 29, OT 21, MF 9, WU 4, GS 2 ...

Every one of the 122 was a class code; not one was a security. That is 19% of
all extracted asset tickers, and `BA` was the single most common "ticker" in the
sample — against 50 occurrences of the `[BA]` bank-account code.

After the fix the same sample yields 517 tickers, losing 120 fabrications and no
real symbol.

This is the mistake D3 records against the old committee detector, which
substring-matched and read "ba" as Alibaba. That fixed the *matching*. This is
the *extraction* still inventing the symbol.

Scope, stated plainly: no detector reads `Asset.ticker` — the wealth analyzer
sums `value_min`/`value_max` only — so no anomaly finding was built on these.
What was affected is `/api/assets`, where `?ticker=BA` returned other people's
bank accounts as Boeing holdings.
"""

from __future__ import annotations

import pytest

from src.parsing.pdf_parser import DisclosureParser


@pytest.fixture(scope="module")
def parser():
    return DisclosureParser()


class TestTheClassCodeIsNotATicker:
    @pytest.mark.parametrize(
        "description,code",
        [
            ("Fifth-Third Bank [BA]", "BA"),
            ("John A. James Children's Trust ⇒ Morgan Stanley Private Bank [BA]", "BA"),
            ("JPMorgan Deposit Acct [BA]", "BA"),
            ("Jamatt Financial, Inc - Tampa FL (K-1) [OT]", "OT"),
            ("VB Pointe West Investments, LLC [RP]", "RP"),
            ("TIAA-CREF Intelligent Life VUL ⇒ DFA Global Bond Portfolio [WU]", "WU"),
            ("US Treasury Bill [GS]", "GS"),
        ],
    )
    def test_a_square_bracket_code_never_becomes_the_ticker(self, parser, description, code):
        assert parser._extract_ticker(description) != code

    @pytest.mark.parametrize(
        "description",
        [
            "i shares tr gbl msci [CS]",
            "United Methodist Personal Investment Plan ⇒ International Equity [OT]",
            "Vanguard Value [EF]",
        ],
    )
    def test_the_keyword_fallback_does_not_harvest_it_either(self, parser, description):
        """These reach the keyword scan rather than the bracket match — "shares",
        "equity" — and it read the bare capitals straight out of the brackets."""
        assert parser._extract_ticker(description) is None


class TestARealTickerStillSurvives:
    def test_a_parenthesised_symbol_is_read(self, parser):
        assert parser._extract_ticker("Apple Inc. (AAPL) [ST]") == "AAPL"

    def test_the_fund_symbol_wins_over_the_class_code(self, parser):
        description = "Lazard International Strategic Equity Ptf Insti Shs (LISIX) [MF]"

        assert parser._extract_ticker(description) == "LISIX"

    def test_boeing_in_parentheses_is_still_boeing(self, parser):
        """The case that proves the rule is the bracket and not the string: the
        very same "BA" is genuine here and fabricated in "Fifth-Third Bank [BA]"."""
        assert parser._extract_ticker("Bill's IRA ⇒ Boeing Company (BA) [ST]") == "BA"

    def test_a_description_with_no_symbol_at_all_yields_none(self, parser):
        assert parser._extract_ticker("Rental property, Springfield IL") is None


class TestTheAssetTypeIsUnaffected:
    """`_determine_asset_type` reads description keywords, not the ticker, and
    must keep classifying these correctly now the symbol is gone."""

    @pytest.mark.parametrize(
        "description,expected",
        [
            ("Fifth-Third Bank [BA]", "bank_account"),
            ("Apple Inc. Common Stock [ST]", "stock"),
            ("Vanguard Index Fund [MF]", "mutual_fund"),
            ("LP Retirement Account ⇒ UBS Bank [BA]", "retirement"),
        ],
    )
    def test_classification_survives(self, parser, description, expected):
        assert parser._determine_asset_type(description) == expected
