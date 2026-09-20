"""Twelve rows were published as "all buys or all sells". They were neither.

Rep. Thomas Kean's PTR 20026021 prints twelve lines dated 30 September 2024,
every one with transaction type `E` and the note:

    D: Holdings in J exchanged out for receipt of holdings in AMTM through a
       corporate action.

Jacobs Solutions spinning off Amentum. The site published:

    Same-direction trades on one day (12)
    Member made 12 trades in the same direction (all buys or all sells) on
    30 September 2024.

The direction was in hand the whole time -- `_same_direction_run` groups by it
-- and the sentence printed a constant instead.

The same `E` reached the frequency count, where it does more than mis-word:

    High trading activity: 15 trades in September 2024

He made one. The other fourteen were Jacobs becoming Amentum.

**What the form's `E` means, measured.** All 46 exchange rows in the 10,594-row
corpus are corporate actions and not one is a discretionary trade: Exxon and
Pioneer, Liberty Media and SiriusXM, Synopsys and Ansys, Capital One and
Discover, Chevron and Hess, the Sandisk, Qnity and Solstice spin-offs, and a
run of municipal refundings. So a detector whose subject is how often a member
*traded* must not count them, and one describing what they did must not call
them buying or selling.

They are named rather than dropped silently -- the rule #99 set for a spouse's
rows -- so a reader checking against the PDF still gets back to the printed
total.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector, Run
from src.analysis.trade_analyzer import TradeAnalyzer
from src.db.models import TransactionType
from tests.test_the_streak_was_the_alphabet import _filing, _member, _trades

B = TransactionType.PURCHASE
S = TransactionType.SALE
E = TransactionType.EXCHANGE


class _Txn:
    def __init__(self, when, kind, tid=1, disclosure_id=1):
        self.transaction_date = when
        self.transaction_type = kind
        self.id = tid
        self.disclosure_id = disclosure_id
        self.description = "Jacobs Solutions Inc. Common Stock (J) [ST]"
        self.ticker = "J"
        self.owner = ""
        self.amount_min = None
        self.amount_max = None


def _rows(n, kind, day=30):
    return [_Txn(datetime(2024, 9, day), kind, tid=i) for i in range(n)]


class TestTheRunCarriesItsDirection:
    def test_the_run_says_what_the_rows_were(self):
        run = ExtendedAnomalyDetector()._same_direction_run(_rows(12, TransactionType.EXCHANGE))
        assert run is not None
        assert run.length == 12
        assert run.direction == TransactionType.EXCHANGE

    @pytest.mark.parametrize(
        "kind", [TransactionType.PURCHASE, TransactionType.SALE, TransactionType.EXCHANGE]
    )
    def test_every_direction_is_carried_not_just_the_odd_one(self, kind):
        assert ExtendedAnomalyDetector()._same_direction_run(_rows(8, kind)).direction == kind

    def test_a_run_with_no_direction_does_not_crash_the_noun(self):
        # `Run` defaults `direction` to None so an older construction still
        # builds; the sentence then falls back to the neutral phrasing rather
        # than asserting a direction nothing supplied.
        from src.analysis.extended_anomaly_detector import _direction_noun, _is_exchange

        bare = Run(length=5, span_days=0, days=1, first=datetime(2024, 9, 30))
        assert not _is_exchange(bare)
        assert _direction_noun(bare) == "trades in the same direction"


class TestTheSentenceIsBuiltFromTheRows:
    def _detail(self, kind, n=12, days=(30,)):
        rows = []
        for i, day in enumerate(days):
            rows += [_Txn(datetime(2024, 9, day), kind, tid=100 * i + j) for j in range(n)]
        run = ExtendedAnomalyDetector()._same_direction_run(rows)
        from src.analysis.extended_anomaly_detector import _direction_noun

        return _direction_noun(run)

    def test_purchases_are_called_purchases(self):
        assert self._detail(TransactionType.PURCHASE) == "purchases"

    def test_sales_are_called_sales(self):
        assert self._detail(TransactionType.SALE) == "sales"

    def test_exchanges_are_called_exchanges(self):
        assert self._detail(TransactionType.EXCHANGE) == "exchanges"


class TestTheCountIsOfTradesThePersonPlaced:
    """`high_trading_frequency`'s subject is how often the member traded."""

    def _findings(self, rows):
        return TradeAnalyzer()._check_trading_frequency(rows, member_id=1, member=None)

    def test_a_corporate_action_month_does_not_publish_high_activity(self):
        # Kean's September 2024, reproduced: one trade, fourteen exchanges.
        rows = _rows(14, TransactionType.EXCHANGE) + [
            _Txn(datetime(2024, 9, 12), TransactionType.PURCHASE, tid=99)
        ]
        assert self._findings(rows) == []

    def test_the_exchanges_are_named_not_dropped_silently(self):
        rows = _rows(12, TransactionType.PURCHASE, day=5) + _rows(2, TransactionType.EXCHANGE)
        found = self._findings(rows)
        assert len(found) == 1
        assert found[0]["title"] == "High trading activity: 12 trades in September 2024"
        assert "2 exchange(s)" in found[0]["description"]
        assert "not trades the member placed" in found[0]["description"]

    def test_a_month_with_no_exchanges_gains_no_clause(self):
        found = self._findings(_rows(12, TransactionType.SALE, day=5))
        assert len(found) == 1
        assert "exchange" not in found[0]["description"]

    def test_the_count_and_the_value_chip_agree(self):
        rows = _rows(12, TransactionType.PURCHASE, day=5) + _rows(3, TransactionType.EXCHANGE)
        found = self._findings(rows)
        assert int(found[0]["computed_value"]) == 12
        assert "12 " in found[0]["description"]


class TestWhatGetsPublished:
    """End to end, because the mis-worded sentence is what reached readers."""

    def _finding(self, db, member):
        found = [
            a
            for a in ExtendedAnomalyDetector().detect_trade_timing_anomalies(db)
            if a["member_id"] == member.id and a["anomaly_type"] == "trade_clustering"
        ]
        return found[0] if found else None

    def test_keans_twelve_are_titled_and_described_as_exchanges(self, db_session):
        member = _member(db_session, "EX00001")
        _trades(
            db_session,
            _filing(db_session, member, "EX-1"),
            datetime(2024, 9, 30),
            [E] * 12,
        )

        found = self._finding(db_session, member)
        assert found is not None
        assert found["title"] == "Exchanges on one day (12)"
        assert "12 exchanges" in found["description"]
        assert "corporate action" in found["description"]
        # The two things the old sentence asserted and this one must not.
        assert "same direction" not in found["description"]
        assert "all buys or all sells" not in found["description"]

    def test_a_sale_run_still_says_sales_and_keeps_its_title(self, db_session):
        member = _member(db_session, "EX00002")
        _trades(db_session, _filing(db_session, member, "EX-2"), datetime(2024, 9, 30), [S] * 9)

        found = self._finding(db_session, member)
        assert found["title"] == "Same-direction trades on one day (9)"
        assert "9 sales" in found["description"]

    def test_a_purchase_run_over_several_days_says_purchases(self, db_session):
        member = _member(db_session, "EX00003")
        filing = _filing(db_session, member, "EX-3")
        for day in (2, 9, 16):
            _trades(db_session, filing, datetime(2024, 9, day), [B] * 4)

        found = self._finding(db_session, member)
        assert found["title"] == "Same-direction trades on 3 days (12)"
        assert "12 purchases" in found["description"]
        assert "exchange" not in found["description"]


def test_the_constant_is_no_longer_emittable():
    """`ast`-based, because the module's docstrings quote the old wording.

    The same technique `test_the_streak_was_the_alphabet.py` uses: read the
    string literals the module can actually emit, excluding docstrings, so a
    comment explaining the defect does not count as the defect.
    """
    import ast

    tree = ast.parse(open("src/analysis/extended_anomaly_detector.py").read())
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
    assert not [s for s in emittable if "all buys or all sells" in s]


def test_the_published_sentence_is_taken_down_rather_than_left_up():
    """`persist_anomalies` only ever inserts, so a corrected sentence needs a purge.

    A run whose length and day count are unchanged keeps its identity under
    `anomaly_key`, so without a needle the old parenthetical is served for ever.
    """
    from src.cli import _SUPERSEDED_WORDING

    assert ("trade_clustering", "description", "all buys or all sells") in _SUPERSEDED_WORDING
