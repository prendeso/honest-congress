"""Two blind slices published unreadable text about named people.

`late_filing` ended every description with `description[:30]` and `large_trade`
titled itself with `description[:20]`, both cutting wherever the character
fell. What went out, verbatim:

    Trade: purchase Cleveland-Cliffs Inc. Common S
    Trade: sale American Funds Income Fund of\\n
    Large transaction: US Treasury Bill [GS purchase (more than $1,000,000)
    Large transaction: Garden of Eden LLC,  sale (more than $1,000,000)

Measured on the corpus: **72 of 174** late-filing findings truncate mid-word
and **11** carry an embedded newline into the published sentence; **59 of 80**
large-trade titles truncate, and three different Treasury bills produce the
SAME title -- saved from colliding only because that finding is keyed on
`transaction_id`.

The second defect is in the same sentence. **93 of the 174 late-filing
findings sit on a row the form attributes to somebody else** -- 91 a spouse,
2 a dependent child -- under a sentence naming the member and nothing else:

    Rep. Kelly, late filing
      the row       owner = Spouse
      published     "Trade: purchase Cleveland-Cliffs Inc. Common S"

Those rows are scored **on purpose** and must stay scored: the STOCK Act duty
is the member's for the whole household, which is why `attribution` exempts
this detector by name and a test pins it. What was missing is the fact, not
the finding. So the sentence says whose the trade was, and why it is still
scored against the member.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import ASSET_NAME_LIMIT, TradeAnalyzer, _asset_name, _whose_trade
from src.db.models import TransactionType


class _Txn:
    def __init__(self, description=None, ticker=None, owner="Self"):
        self.description = description
        self.ticker = ticker
        self.owner = owner
        self.transaction_type = TransactionType.PURCHASE
        self.amount_min = Decimal("1001")
        self.amount_max = Decimal("15000")
        self.transaction_date = datetime(2024, 4, 8)


class TestTheNameIsReadable:
    def test_a_ticker_wins_when_the_filing_gave_one(self):
        assert _asset_name(_Txn("Apple Inc. - Common Stock", ticker="AAPL")) == "AAPL"

    def test_a_short_name_is_left_exactly_as_it_is(self):
        assert _asset_name(_Txn("Vanguard Mid Cap ETF")) == "Vanguard Mid Cap ETF"

    def test_nothing_is_cut_in_the_middle_of_a_word(self):
        # The Kelly row, at the old 30-character slice: "...Common S".
        name = _asset_name(_Txn("Cleveland-Cliffs Inc. Common Stock"))
        assert name == "Cleveland-Cliffs Inc. Common Stock"

        long = _asset_name(_Txn("Business Entity Company: Arp & Hammond Hardware Company LLC 50%"))
        assert long.endswith("...")
        assert not long.rstrip(".").endswith(("Compan", "Hardwar", "L"))
        assert len(long) <= ASSET_NAME_LIMIT + 3

    def test_an_embedded_newline_never_reaches_the_sentence(self):
        # The weak text path leaves these in the stored description.
        name = _asset_name(_Txn("United States Treasury Bills CUSIP\n912797GZ4 due 4/4/24"))
        assert "\n" not in name
        assert "CUSIP 912797GZ4" in name

    def test_the_asset_class_tag_is_not_part_of_the_name(self):
        # "US Treasury Bill [GS" is the shape that makes the case. D21.
        assert _asset_name(_Txn("US Treasury Bill [GS]")) == "US Treasury Bill"

    def test_a_row_with_no_description_says_so_rather_than_nothing(self):
        assert _asset_name(_Txn(None)) == "unknown"
        assert _asset_name(_Txn("   ")) == "unknown"

    def test_a_trailing_comma_or_dash_is_not_left_dangling(self):
        name = _asset_name(
            _Txn("Garden of Eden LLC, a Wyoming limited liability company, 100%"), 22
        )
        assert not name.rstrip(".").endswith((",", "-", ";", ":"))


class TestTheSentenceSaysWhoseTradeItWas:
    def test_a_spouses_row_is_named_and_the_duty_is_explained(self):
        clause = _whose_trade(_Txn("X", owner="Spouse"))
        assert "their spouse" in clause
        assert "deadline is the member's" in clause
        assert "scored against them" in clause

    def test_a_dependent_childs_row_is_named(self):
        assert "a dependent child" in _whose_trade(_Txn("X", owner="Dependent Child"))

    @pytest.mark.parametrize("owner", ["Self", "Joint", "", None])
    def test_the_members_own_row_gains_no_clause(self, owner):
        # Joint is the member's, per #99 -- they are a party to it.
        assert _whose_trade(_Txn("X", owner=owner)) == ""

    def test_coverage_is_unchanged_because_the_duty_is_the_members(self):
        """The finding must not disappear; only the sentence changes.

        `attribution.py` exempts `late_filing` by name and
        `test_a_spouses_trade_is_not_the_members.py` pins it. This is the
        sentence-level counterpart: naming the owner must not become filtering
        on it.
        """
        import ast
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(TradeAnalyzer._check_late_filings))
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "held_by_member" not in names, "late_filing must not filter on the owner"
        assert "trades_the_member_holds" not in names


class TestWhatGetsPublished:
    def test_the_old_lead_in_is_gone_and_the_new_one_reads(self):
        rows = TradeAnalyzer()._build_large_trade_text(
            _Txn("US Treasury Bill 912797GZ4 due 4/4/24 [GS]")
        )
        assert "[GS" not in rows["title"]
        assert rows["title"].startswith("Large transaction: US Treasury Bill 912797GZ4 due 4/4/24")

    def test_a_large_trade_title_fits_the_column(self):
        long = _Txn("Business Entity Company: " + "Wyoming Land and Cattle Partners " * 6)
        title = TradeAnalyzer()._build_large_trade_text(long)["title"]
        assert len(title) <= 200

    def test_two_different_bills_no_longer_share_a_title(self):
        a = TradeAnalyzer()._build_large_trade_text(_Txn("US Treasury Bill 912797GZ4 due 4/4/24"))
        b = TradeAnalyzer()._build_large_trade_text(_Txn("US Treasury Bill 912797HB2 due 6/6/24"))
        assert a["title"] != b["title"]


def test_the_published_sentence_is_taken_down():
    """Keyed on `transaction_id`, so the identity survives the rewording.

    `persist_anomalies` only ever inserts, so without a needle every stored
    late-filing finding keeps the truncated text for ever.
    """
    from src.cli import _SUPERSEDED_WORDING

    assert ("late_filing", "description", "Trade: ") in _SUPERSEDED_WORDING


def test_the_catalogue_says_what_over_half_of_them_rest_on():
    from src.analysis.catalog import DETECTORS

    caveat = next(d for d in DETECTORS if d.anomaly_type == "late_filing").limits
    assert "spouse" in caveat
    assert "deadline is still the member's" in caveat
