"""The count was right and the noun was invented.

`high_trading_frequency` published, about named senators:

    18 stock trades were disclosed in March 2024

Sen. Rick Scott made zero. Every one was a municipal bond. The same sentence
went out about Sen. Warner's eighteen, and about Sen. Fetterman's, which were
bonds held in a child's account.

Nothing checked the word. `Transaction` has no `asset_type` column -- that
lives on `Asset`, which holds Schedule A positions, not PTR rows -- so every
transaction reached the detector unlabelled and every one was called a stock
trade.

The form says which is which. The House PTR prints an asset-class code in
brackets after the asset name and `ptr_parser` keeps it in the description.
Only `[GS]` and `[ST]` are given meanings here, both grounded in the corpus's
own descriptions rather than in a legend the PTRs do not print.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.asset_class import (
    all_fixed_income,
    class_code,
    fixed_income_count,
    is_fixed_income,
)
from src.analysis.trade_analyzer import TradeAnalyzer


class _Row:
    def __init__(self, description):
        self.description = description


def _fixed(description):
    return is_fixed_income(_Row(description))


# Descriptions taken verbatim from a 10,594-row corpus of real House PTRs.
GOVERNMENT = [
    "US Treasury Bill 912797GD3 [GS]",
    "U.S. Treasury Bond [GS]",
    "Chicago Ill O'Hare 4.00% 01/01/36 [GS]",
    "Putnam Cnty WVA Brd Ed 1.123% 05/01/24 [GS]",
    "Los Angeles CA GO UTX [GS]",
]
SHARES = [
    "Albemarle Corporation (ALB) [ST]",
    "Microsoft Corporation - Common Stock (MSFT) [ST]",
    "Alphabet Inc. - Class C Capital Stock (GOOG) [ST]",
]


class TestThePrintedCodeDecides:
    @pytest.mark.parametrize("description", GOVERNMENT)
    def test_a_government_security_is_debt(self, description):
        assert _fixed(description)

    @pytest.mark.parametrize("description", SHARES)
    def test_a_share_is_not(self, description):
        assert not _fixed(description)

    def test_the_code_beats_the_wording(self):
        """A row the form labelled `[ST]` cannot be talked out of being a share
        by a word in its name.

        Measured: exactly one `[ST]` row in 4,371 matches the fallback wording
        -- "Vanguard Short-Term Corporate Bond ETF (VCSH) [ST]" -- and the code
        is what settles it.
        """
        assert not _fixed("Vanguard Short-Term Corporate Bond ETF (VCSH) [ST]")

    def test_the_last_bracket_is_the_asset_class(self):
        """The class trails the name, so an earlier bracket must not win.

        The first version of this used a one-letter earlier bracket, which the
        two-letter pattern never matched -- so it passed with the code reading
        `codes[0]` and the mutation survived. Both brackets here are real
        two-letter codes, which is the only shape that tests anything.
        """
        assert class_code(_Row("Some Muni Fund [MF] Series A (ABC) [GS]")) == "GS"
        assert class_code(_Row("Growth Fund [HN] (XYZ) [ST]")) == "ST"
        assert class_code(_Row("No code here (ABC)")) is None

    def test_an_earlier_bracket_cannot_decide_the_instrument(self):
        # The end-to-end of the same thing: a share whose NAME carries another
        # code must not be published as debt.
        assert not _fixed("Growth Fund [HN] (XYZ) [ST]")
        assert _fixed("Some Muni Fund [MF] Series A (ABC) [GS]")


class TestTheFallbackOnlyRunsWhenTheCodeIsMissing:
    """51.8% of rows carry no code: the weaker text path truncates the
    description before the trailing `[XX]`."""

    @pytest.mark.parametrize(
        "description",
        [
            "US Treasury Bill Due",
            "U.S. Treasury Note due 10/31/2027",
            "Municipal Bond Fund",
            "New Jersey ST Transn TR FD Auth Transn",  # no wording match
        ],
    )
    def test_it_reads_the_wording(self, description):
        expected = "Treasury" in description or "Municipal" in description
        assert _fixed(description) is expected

    def test_an_unclassifiable_row_is_not_claimed_as_debt(self):
        """It under-claims rather than over-claims, deliberately.

        A missed bond leaves a finding worded as it is today. A misclassified
        share would put a false noun in a sentence about a named person.
        """
        assert not _fixed("Eastside Utility District (Hamilton County, TN)")
        assert not _fixed("")

    def test_a_bare_coupon_is_not_enough(self):
        """Tested and rejected: it lifts [GS] recall to 74% but drags in 13
        preferred-share rows the filers coded [ST]."""
        assert not _fixed("Ellington Financial Inc. 7.00% Series D Cumulative Perpetual")
        assert not _fixed("Cadence Bank 5.50% Series A (CADE$A)")


class TestTheAggregates:
    def test_all_fixed_income_needs_every_row(self):
        assert all_fixed_income([_Row(d) for d in GOVERNMENT])
        assert not all_fixed_income([_Row(GOVERNMENT[0]), _Row(SHARES[0])])

    def test_an_empty_list_is_not_all_anything(self):
        assert not all_fixed_income([])

    def test_the_count_is_available_for_a_mixed_month(self):
        rows = [_Row(d) for d in GOVERNMENT + SHARES]
        assert fixed_income_count(rows) == len(GOVERNMENT)


# ---------------- what gets published ----------------


class _Txn:
    def __init__(self, when, description):
        self.disclosure_id = 7
        self.transaction_date = when
        self.ticker = None
        self.transaction_type = None
        self.amount_min = Decimal("1001")
        self.amount_max = Decimal("15000")
        self.description = description


def _finding(description, count=18):
    start = datetime(2024, 3, 1)
    rows = [_Txn(start + timedelta(days=i % 27), description) for i in range(count)]
    return TradeAnalyzer()._check_trading_frequency(rows, member_id=1, member=None)[0]


class TestTheSentenceThatGoesOut:
    """The noun is what this file is for. The verb moved later, and why is
    worth recording: "disclosed in <month>" was replaced by "attributed to this
    member in <month>" once a neutral audit pointed out that the count is the
    member's OWN rows while the filing prints the whole household -- Rep.
    Donalds' March 2025 filing shows 48 transactions against a published 23.
    Right number, unverifiable sentence. The assertions below track that
    change; what they exist to pin, that no bond is called a stock, is
    untouched."""

    def test_rick_scotts_eighteen_are_not_called_stock_trades(self):
        finding = _finding("Chicago Ill O'Hare 4.00% 01/01/36 [GS]")

        assert "stock" not in finding["description"].lower()
        assert "18 government or municipal securities were attributed" in finding["description"]

    def test_a_month_of_shares_says_transactions_not_stock(self):
        # "stock" is not restored for equities either. The detector counts PTR
        # rows; what it can say honestly is how many there were.
        finding = _finding("Albemarle Corporation (ALB) [ST]")

        assert "18 transactions were attributed" in finding["description"]
        assert "stock" not in finding["description"].lower()

    def test_a_mixed_month_claims_nothing_about_the_instruments(self):
        start = datetime(2024, 3, 1)
        rows = [
            _Txn(start + timedelta(days=i), "US Treasury Bill 912797GD3 [GS]") for i in range(9)
        ]
        rows += [
            _Txn(start + timedelta(days=i), "Albemarle Corporation (ALB) [ST]") for i in range(9)
        ]

        finding = TradeAnalyzer()._check_trading_frequency(rows, member_id=1, member=None)[0]

        assert "18 transactions were attributed" in finding["description"]
        assert "securities" not in finding["description"]

    def test_the_count_is_still_exact(self):
        finding = _finding("US Treasury Bill 912797GD3 [GS]", count=41)

        assert "41 trades" in finding["title"]
        assert "41 government or municipal securities" in finding["description"]
        assert int(finding["computed_value"]) == 41
