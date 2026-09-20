"""Three faults in one sentence, over a five-row filing.

`sector_concentration` published, about a named member:

    High concentration in finance sector (60-75%)
    A significant portion of trades (60-75%) are concentrated in the finance
    sector. This unusual concentration may warrant further review.

The arithmetic was right -- three of her five rows are finance. Everything
around it was not.

**No denominator and no scope.** "A significant portion of trades" is a share
of nothing a reader can name. The scope is ONE PTR: five rows over three
weeks, not a career. With the filing open, nothing reconciles.

**"Unusual" is measured by nothing.** `sector_concentration` is in
`significance.NO_NULL_MODEL`, so no null distribution exists for it, and since
#100 the only thing entitled to grade extremity is `percentile_rank` -- which
put this finding at the **40th percentile** of its own type while the sentence
called it unusual. D3, D10 and D14 were each spent deleting a claim of exactly
this shape; it survived here.

**The band hid a number already on the card.** `computed_value` is the exact
percentage and renders as the "Value" chip two lines below the title, so
"60-75%" was published beside "60 percent". D5 does not reach this: it governs
figures DERIVED from disclosed amount ranges, and a count of rows in a sector
is disclosed exactly -- the same argument #104 used to take the band out of
the frequency title.

And the denominator is the member's own rows, per #99, so a filing printing
more lines than the finding counts needs the same clause the frequency check
carries.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import TradeAnalyzer, _percent
from src.db.models import TransactionType


class _Txn:
    def __init__(self, ticker, owner="Self", disclosure_id=1, tid=1):
        self.ticker = ticker
        self.description = f"{ticker} - Common Stock"
        self.owner = owner
        self.disclosure_id = disclosure_id
        self.id = tid
        self.transaction_date = datetime(2025, 4, 8)
        self.transaction_type = TransactionType.PURCHASE
        self.amount_min = Decimal("1001")
        self.amount_max = Decimal("15000")


class _Index:
    """Stands in for `SectorIndex`, so the test is about the sentence."""

    def __init__(self, mapping):
        self.mapping = mapping

    def classify(self, ticker, description):
        return self.mapping.get(ticker, [])


FINANCE = _Index({"SCHW": ["finance"], "WTM": ["finance"], "RF": ["finance"]})


def _finding(rows, disclosed=None):
    found = TradeAnalyzer()._check_sector_concentration(
        rows, member_id=1, member=None, index=FINANCE, disclosed=disclosed
    )
    return found[0] if found else None


def _five_rows():
    # Julie Johnson's filing: three finance, two not.
    return [
        _Txn("SCHW", tid=1),
        _Txn("WTM", tid=2),
        _Txn("RF", tid=3),
        _Txn("AAPL", tid=4),
        _Txn("MSFT", tid=5),
    ]


class TestTheSentenceNamesWhatItCounted:
    def test_the_count_the_total_and_the_scope_are_all_published(self):
        found = _finding(_five_rows())
        assert "3 of the 5 transactions attributed to this member" in found["description"]
        assert "in this filing" in found["description"]

    def test_the_title_carries_the_exact_fact_not_a_band(self):
        assert _finding(_five_rows())["title"] == "High concentration in finance sector (3 of 5)"

    def test_the_exact_share_is_published_beside_the_value_chip(self):
        found = _finding(_five_rows())
        assert "(60%)" in found["description"]
        assert float(found["computed_value"]) == pytest.approx(60.0)


class TestTheClaimNothingMeasures:
    @pytest.mark.parametrize(
        "phrase",
        [
            "unusual",
            "may warrant further review",
            "A significant portion",
            "60-75%",
            "over 50%",
            "75-90%",
            "over 90%",
        ],
    )
    def test_the_old_phrasing_is_gone(self, phrase):
        assert phrase not in _finding(_five_rows())["description"]

    def test_no_band_survives_anywhere_in_the_module(self):
        """`ast`-based: the docstrings quote the bands on purpose."""
        import ast

        tree = ast.parse(open("src/analysis/trade_analyzer.py").read())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        emittable = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
        for band in ("60-75%", "75-90%", "over 90%", "This unusual concentration"):
            assert not [s for s in emittable if band in s], band


class TestTheDenominatorIsReconcilable:
    def test_the_filings_other_rows_are_named_not_dropped_silently(self):
        own = _five_rows()
        spouse = [_Txn("KO", owner="Spouse", tid=10 + i) for i in range(7)]
        found = _finding(own, disclosed=own + spouse)
        assert "3 of the 5 transactions" in found["description"]
        assert "7 transaction(s) belonging to their spouse" in found["description"]
        assert "which this count excludes" in found["description"]

    def test_a_filing_with_nothing_excluded_gains_no_clause(self):
        own = _five_rows()
        found = _finding(own, disclosed=list(own))
        assert "excludes" not in found["description"]

    def test_the_clause_is_optional_so_older_callers_still_work(self):
        assert _finding(_five_rows(), disclosed=None) is not None


class TestThePercentIsExact:
    @pytest.mark.parametrize(
        "value,expected",
        [(60.0, "60%"), (75.0, "75%"), (71.42857, "71.4%"), (57.142857, "57.1%"), (80.6, "80.6%")],
    )
    def test_one_decimal_only_where_there_is_one(self, value, expected):
        assert _percent(value) == expected


def test_the_published_sentence_is_taken_down():
    from src.cli import _SUPERSEDED_WORDING

    needles = {n for kind, field, n in _SUPERSEDED_WORDING if kind == "sector_concentration"}
    assert "This unusual concentration" in needles
    assert "A significant portion of trades" in needles
