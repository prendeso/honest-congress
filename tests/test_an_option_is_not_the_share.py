"""The site said a member bought $1,000,000 of Microsoft. They bought calls.

A House PTR names the UNDERLYING on an option row and marks the row `[OP]`:

    Microsoft Corporation - Common Stock (MSFT) [OP]   $1,000,001 - $5,000,000
    D: Call options; Strike price $240; Expires 9/19/2025

`large_trade` built its sentence from the asset name alone and published

    Large transaction: MSFT purchase (more than $1,000,000)
    A purchase of MSFT worth more than $1,000,000 was reported.

which describes a stock purchase that did not happen. **13 of the corpus's 80
large-trade findings sit on such a row.**

The amount needs saying too. On an option row the disclosed band is the
transaction's own value, not the value of the shares it controls, and the two
differ by roughly the leverage — so "more than $1,000,000" beside a company
name invites the second reading.

**The code is the only signal used.** All 28 `[OP]` rows in the corpus print a
Description sub-line naming the derivative — "Call options; Strike price $240",
"Put option, strike price $215", "Purchased 50 call options with a strike price
of $200" — across ten filings, so the code is grounded the way #100 grounded
`[GS]` and `[ST]`. There is deliberately **no wording fallback**: "call" and
"put" are far too common in free text to carry a positive claim about a named
person's trade.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.analysis.asset_class import OPTION, class_code, is_option, option_count
from src.analysis.trade_analyzer import TradeAnalyzer
from src.db.models import TransactionType


class _Txn:
    def __init__(self, description, ticker=None, kind=TransactionType.PURCHASE):
        self.description = description
        self.ticker = ticker
        self.transaction_type = kind
        self.amount_min = Decimal("1000001")
        self.amount_max = Decimal("5000000")


# Verbatim from the corpus.
OPTIONS = [
    "Microsoft Corporation - Common Stock (MSFT) [OP]",
    "Palo Alto Networks, Inc. (PANW) [OP]",
    "Berkshire Hathaway Inc. New Common Stock (BRK.B) [OP]",
    "Broadcom Inc. - Common Stock (AVGO) [OP]",
    "NVIDIA Corporation - Common Stock (NVDA) [OP]",
    "Tempus AI, Inc. - Class A Common Stock (TEM) [OP]",
]
NOT_OPTIONS = [
    "Microsoft Corporation - Common Stock (MSFT) [ST]",
    "US Treasury Bill 912797GD3 [GS]",
    "Apple Inc. - Common Stock",
]


class TestTheFormsOwnCode:
    @pytest.mark.parametrize("description", OPTIONS)
    def test_every_op_row_in_the_corpus_reads_as_an_option(self, description):
        assert class_code(_Txn(description)) == OPTION
        assert is_option(_Txn(description))

    @pytest.mark.parametrize("description", NOT_OPTIONS)
    def test_nothing_else_does(self, description):
        assert not is_option(_Txn(description))

    @pytest.mark.parametrize(
        "description",
        [
            "Call of the Wild Growth Fund [ST]",
            "Putnam Investments Global Equity",
            "T. Rowe Price Call Income Fund",
            "Shares purchased under a call provision",
        ],
    )
    def test_the_word_alone_never_asserts_it(self, description):
        """No wording fallback, on purpose: the words are too common."""
        assert not is_option(_Txn(description))

    def test_an_unlabelled_row_stays_unlabelled(self):
        assert not is_option(_Txn(None))
        assert not is_option(_Txn(""))

    def test_the_count_helper_agrees_with_the_predicate(self):
        rows = [_Txn(d) for d in OPTIONS + NOT_OPTIONS]
        assert option_count(rows) == len(OPTIONS)


class TestWhatGetsPublished:
    def _text(self, description, ticker=None, kind=TransactionType.PURCHASE):
        return TradeAnalyzer()._build_large_trade_text(_Txn(description, ticker, kind))

    def test_the_trade_is_named_as_options_not_as_the_share(self):
        text = self._text("Microsoft Corporation - Common Stock (MSFT) [OP]", ticker="MSFT")
        assert text["title"] == "Large transaction: MSFT options purchase (more than $1,000,000)"
        assert "A purchase of MSFT options worth more than $1,000,000" in text["description"]

    def test_the_amount_is_said_to_be_the_transactions_not_the_shares(self):
        text = self._text("NVIDIA Corporation - Common Stock (NVDA) [OP]", ticker="NVDA")
        assert "not the value of the underlying shares" in text["description"]
        assert "the asset named is the underlying" in text["description"]

    def test_a_sale_reads_the_same_way(self):
        text = self._text(
            "Microsoft Corporation - Common Stock (MSFT) [OP]",
            ticker="MSFT",
            kind=TransactionType.SALE,
        )
        assert text["title"] == "Large transaction: MSFT options sale (more than $1,000,000)"

    def test_a_share_purchase_is_untouched(self):
        text = self._text("Microsoft Corporation - Common Stock (MSFT) [ST]", ticker="MSFT")
        assert text["title"] == "Large transaction: MSFT purchase (more than $1,000,000)"
        assert "options" not in text["description"]
        assert "underlying" not in text["description"]

    def test_the_standing_caveat_survives_either_way(self):
        for description in ("... (MSFT) [OP]", "... (MSFT) [ST]"):
            assert (
                "Large transactions warrant additional scrutiny."
                in (self._text(description, ticker="MSFT")["description"])
            )


def test_the_published_rows_are_rewritten_rather_than_purged():
    """No needle needed here, and the reason is worth pinning.

    `large_trade` is a TRADE-level finding, keyed on `transaction_id`, and
    `_sync_large_trade_anomalies` already rewrites the title and description of
    a stored row whose text no longer matches what the detector produces. That
    is the one detector where a reworded sentence corrects itself.
    """
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(TradeAnalyzer._sync_large_trade_anomalies))
    tree = ast.parse(source)
    assigned = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
    }
    assert {"title", "description"} <= assigned, (
        "the large-trade sync no longer rewrites stored text, so a reworded "
        "sentence would need a purge needle instead"
    )
