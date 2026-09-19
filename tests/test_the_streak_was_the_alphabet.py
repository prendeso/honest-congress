"""A PTR records a date, not a time. Trades sharing a date have no order.

Rep. Blake Moore was published as:

    Consecutive same-direction trades (30 in a row)
    Member made 30 consecutive trades in the same direction (all buys or all
    sells) over 10 days.

37 of his 39 disclosed trades share one date, 2024-01-19. The detector
manufactured a sequence out of them by tie-breaking on `Transaction.id`, which
is PDF row order, which is the Clerk's alphabetical listing by asset name:
Alibaba, Alphabet, Amazon, American Express, Apple, Bank of America, Berkshire
... SPDR, Vanguard, Walt Disney.

His only purchases that day were SPDR S&P 500 and Vanguard Growth ETF -- "S"
and "V" -- so the alphabet pushed every buy to the end and stacked the sales in
front. Verified against the corpus: the direction string for that date reads

    SSSSSSSSSSSSSSSSSSSSSSSSSSSSS BBBBBB SS

The streak was the alphabet. The underlying event was a single-day liquidation
of individual positions into two broad-market index funds -- the divestment
pattern ethics reformers recommend.

So a date contributes to a run only if every trade on it goes the same way. A
mixed date orders nothing and breaks the run. Within a unanimous date the count
is a fact about the day, not a sequence, and the published sentence says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.extended_anomaly_detector import (
    CONSECUTIVE_TRADE_WINDOW_DAYS,
    MIN_CONSECUTIVE_TRADES,
    ExtendedAnomalyDetector,
)
from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="Alpha",
        last_name="Betical",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="UT",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _filing(db, member, doc):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id=doc,
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _trades(db, disclosure, when, kinds):
    """Rows in the order the Clerk prints them, which is alphabetical."""
    for i, kind in enumerate(kinds):
        db.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=when,
                transaction_type=kind,
                description=f"{chr(ord('A') + i % 26)}{i:03d} Holding - Common Stock",
                ticker=f"T{i:03d}",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
                owner="Self",
            )
        )
    db.commit()


def _run(db, member):
    from src.analysis.restatements import member_transactions

    return ExtendedAnomalyDetector()._same_direction_run(member_transactions(db, member.id))


S = TransactionType.SALE
B = TransactionType.PURCHASE


class TestASingleDayOrdersNothing:
    def test_blake_moores_shape_produces_no_run_at_all(self, db_session):
        # 29 sales, 6 purchases, 2 sales -- one date, one liquidation.
        member = _member(db_session, "AL00001")
        _trades(
            db_session,
            _filing(db_session, member, "AL-1"),
            datetime(2024, 1, 19),
            [S] * 29 + [B] * 6 + [S] * 2,
        )

        assert _run(db_session, member) is None

    def test_reordering_the_same_day_cannot_change_the_answer(self, db_session):
        """The defect in one sentence: the answer depended on the sort."""
        member = _member(db_session, "AL00002")
        # Every sale first, then every purchase -- the alphabetical layout that
        # produced "30 in a row". Same multiset, same date.
        _trades(
            db_session,
            _filing(db_session, member, "AL-2"),
            datetime(2024, 1, 19),
            [S] * 31 + [B] * 6,
        )

        assert _run(db_session, member) is None

    def test_a_unanimous_day_still_counts_but_is_not_a_streak(self, db_session):
        member = _member(db_session, "AL00003")
        _trades(db_session, _filing(db_session, member, "AL-3"), datetime(2024, 1, 19), [B] * 8)

        run = _run(db_session, member)
        assert run is not None
        assert run.length == 8
        assert run.days == 1
        assert run.span_days == 0


class TestWhatGetsPublished:
    def _finding(self, db, member):
        return [
            a
            for a in ExtendedAnomalyDetector().detect_trade_timing_anomalies(db)
            if a["member_id"] == member.id and a["anomaly_type"] == "trade_clustering"
        ]

    def test_a_one_day_batch_is_never_called_a_streak(self, db_session):
        member = _member(db_session, "AL00004")
        _trades(db_session, _filing(db_session, member, "AL-4"), datetime(2024, 1, 19), [B] * 8)

        found = self._finding(db_session, member)
        assert len(found) == 1
        text = f"{found[0]['title']} {found[0]['description']} {found[0]['pattern']}"

        for banned in ("in a row", "consecutive", "streak"):
            assert banned not in text.lower(), (
                f"{banned!r} asserts an order the filing does not record"
            )
        assert "on one day" in found[0]["title"]
        assert "no time of day" in found[0]["description"]

    def test_a_multi_day_run_says_how_many_days_it_traded_on(self, db_session):
        # The other half of "over 10 days": a span of ten days that is really
        # one day of trading must not read the same as ten days of trading.
        member = _member(db_session, "AL00005")
        filing = _filing(db_session, member, "AL-5")
        for offset in range(MIN_CONSECUTIVE_TRADES):
            _trades(db_session, filing, datetime(2024, 1, 1) + timedelta(days=offset * 2), [B])

        found = self._finding(db_session, member)
        assert len(found) == 1
        assert f"on {MIN_CONSECUTIVE_TRADES} days" in found[0]["title"]
        assert "consecutive" not in found[0]["description"].lower()


class TestTheRunRuleItself:
    def test_a_mixed_day_breaks_a_run_that_would_span_it(self, db_session):
        member = _member(db_session, "AL00006")
        filing = _filing(db_session, member, "AL-6")
        _trades(db_session, filing, datetime(2024, 1, 1), [B] * 3)
        _trades(db_session, filing, datetime(2024, 1, 2), [B, S])  # mixed
        _trades(db_session, filing, datetime(2024, 1, 3), [B] * 3)

        # Six purchases sit either side of the mixed day; neither side reaches
        # the threshold alone, and the mixed day may not join them.
        assert _run(db_session, member) is None

    def test_unanimous_days_either_side_of_the_window_do_not_join(self, db_session):
        member = _member(db_session, "AL00007")
        filing = _filing(db_session, member, "AL-7")
        _trades(db_session, filing, datetime(2024, 1, 1), [B] * 3)
        _trades(
            db_session,
            filing,
            datetime(2024, 1, 1) + timedelta(days=CONSECUTIVE_TRADE_WINDOW_DAYS + 1),
            [B] * 3,
        )

        assert _run(db_session, member) is None

    def test_a_direction_change_across_days_starts_a_new_run(self, db_session):
        member = _member(db_session, "AL00008")
        filing = _filing(db_session, member, "AL-8")
        _trades(db_session, filing, datetime(2024, 1, 1), [B] * 4)
        _trades(db_session, filing, datetime(2024, 1, 2), [S] * 6)

        run = _run(db_session, member)
        assert run is not None
        assert run.length == 6, "the sales, not the four buys before them"
        assert run.days == 1


@pytest.mark.parametrize("phrase", ["in a row", "within a short period", "consecutive trades"])
def test_the_old_wording_is_unwritable(phrase):
    """The strings the corrected detector can no longer PRODUCE.

    Docstrings are excluded deliberately: this module quotes both old phrases
    at length to record what was published and why it was wrong, and a rule
    that forbade saying so would delete the explanation along with the bug.
    What must not survive is a literal the code can emit -- so the check reads
    string constants and f-string pieces, and skips the docstring of every
    module, class and function.
    """
    import ast
    import inspect

    import src.analysis as analysis
    import src.analysis.extended_anomaly_detector as ext

    # Both modules: `_build_title` in `src.analysis` is the fallback for a
    # finding dict that arrives without a title, and it carried the old
    # wording verbatim -- a live path straight back to the sentence the
    # detector stopped writing.
    tree = ast.parse(inspect.getsource(ext) + "\n" + inspect.getsource(analysis._build_title))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    emittable = [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    offenders = [text for text in emittable if phrase in text]
    assert not offenders, f"the detector can still write {phrase!r}: {offenders}"
