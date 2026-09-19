"""An amendment restates its original, and every detector counted it twice.

A member who amends a disclosure does not file the difference -- they file the
whole thing again, and both row sets are stored. Confirmed against the live API:

    disc 2426  'Annual Report for CY 2024'               Thom Tillis  32 txns
    disc 2859  'Annual Report for CY 2024 (Amendment 1)' Thom Tillis  33 txns
    identical (date, description, amount) rows: 32 of 32

Nothing links an amendment to what it amends: `Disclosure` has no `amends_id`,
`is_amendment` or version column, House filing types are undocumented single
letters, and the Senate stores a free-text label it never parses. So the rule is
content-based, and these tests pin the two properties that carry the weight:
identical rows in DIFFERENT filings collapse, identical rows in the SAME filing
do not, and the surviving copy is the earliest-filed one.

That last property is not tidiness. `late_filing` scores a trade against the
filing that reports it, so keeping the amendment instead would re-score an
already-timely trade as late -- a false accusation about a named person, which
`src/analysis/catalog.py` describes in prose while nothing acted on it.

Names and dates below are the real ones.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.restatements import (
    content_key,
    drop_restatements,
    member_transactions,
    transactions_by_member,
)
from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType


@pytest.fixture
def db(db_session):
    for table in (Transaction, Disclosure, Member):
        db_session.query(table).delete()
    db_session.commit()
    return db_session


def member(db, bioguide="T000476", last="Tillis"):
    row = Member(
        bioguide_id=bioguide,
        first_name="Thom",
        last_name=last,
        chamber=Chamber.SENATE,
        party=Party.REPUBLICAN,
        state="NC",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def filing(db, who, filing_type, filed, doc):
    row = Disclosure(
        member_id=who.id,
        filing_year=2024,
        filing_type=filing_type,
        filing_date=filed,
        document_id=doc,
        document_url=f"https://example.invalid/{doc}",
        is_ptr=False,
        parsed=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def trade(
    db,
    disclosure,
    description="North Carolina Stable Value Fund",
    when=datetime(2024, 4, 22),
    kind=TransactionType.SALE,
    low="1001",
    high="15000",
    owner="Self",
    ticker=None,
):
    row = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=kind,
        description=description,
        ticker=ticker,
        amount_min=Decimal(low),
        amount_max=Decimal(high),
        owner=owner,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# The seven sales Tillis disclosed on 2024-04-22, restated by his amendment.
TILLIS_SALES = [
    "North Carolina Stable Value Fund",
    "North Carolina Treasury Inflation Protected S...",
    "North Carolina Fixed Income Fund",
    "North Carolina Large Cap Index Fund",
    "North Carolina Small/Mid Cap Core Fund",
    "North Carolina International Fund",
    "North Carolina Inflation Responsive Fund",
]


@pytest.fixture
def tillis(db):
    """The live shape: an annual report and its amendment, same seven sales."""
    who = member(db)
    original = filing(db, who, "Annual Report for CY 2024", datetime(2025, 8, 12), "S-2426")
    amendment = filing(
        db, who, "Annual Report for CY 2024 (Amendment 1)", datetime(2026, 7, 26), "S-2859"
    )
    for description in TILLIS_SALES:
        trade(db, original, description=description)
        trade(db, amendment, description=description)
    return who, original, amendment


class TestARestatementIsNotASecondTrade:
    def test_the_amendment_copies_collapse(self, db, tillis):
        who, _, _ = tillis

        kept = member_transactions(db, who.id)

        assert len(kept) == 7, "14 rows are 7 trades filed twice"

    def test_the_surviving_copy_is_the_earliest_filing(self, db, tillis):
        """What repairs late_filing: the trade is scored against the filing that
        FIRST reported it, which is the one the STOCK Act deadline runs against.
        Keeping the amendment would make a timely trade look 15 months late."""
        who, original, _ = tillis

        kept = member_transactions(db, who.id)

        assert {t.disclosure_id for t in kept} == {original.id}

    def test_a_timely_trade_stays_timely_when_restated_later(self, db):
        """`_check_late_filings` filters `is_ptr == True`, so the annual pair
        above never reaches it -- this needs PTRs. PTR restatements are
        confirmed to exist (34 identical trades across two PTRs in a ten-member
        sample), though every live pair found so far was filed the same day and
        so shifts no deadline. This pins the property that protects against the
        case where they are not: the trade is scored against the filing that
        FIRST reported it."""
        who = member(db, bioguide="L000001", last="Late")
        traded = datetime(2025, 1, 10)
        original = filing(db, who, "PTR", datetime(2025, 2, 5), "L-1")
        original.is_ptr = True
        restated = filing(db, who, "PTR", datetime(2026, 3, 1), "L-2")
        restated.is_ptr = True
        db.commit()
        # Three rows, because a PTR carries no amendment label and the rule
        # requires filing-level evidence before it will drop anything.
        for i in range(3):
            trade(db, original, description=f"Holding {i}", when=traded)
            trade(db, restated, description=f"Holding {i}", when=traded)

        kept = member_transactions(db, who.id, ptr_only=True)

        assert len(kept) == 3
        scored_against = original.filing_date
        assert (scored_against - traded).days == 26, "inside the 45-day deadline"
        assert (restated.filing_date - traded).days > 45, (
            "had the restatement won, the same timely trade would read as 415 days late"
        )

    def test_every_disclosed_trade_survives_exactly_once(self, db, tillis):
        who, _, _ = tillis

        kept = member_transactions(db, who.id)

        assert sorted(t.description for t in kept) == sorted(TILLIS_SALES)


class TestTwoRealTradesInOneFilingBothSurvive:
    """The error this rule must not make. A member can buy the same stock twice
    in a day in the same band; collapsing those deletes a disclosed trade, which
    is worse than the overcount being fixed."""

    def test_identical_rows_in_a_single_filing_are_kept(self, db):
        who = member(db, bioguide="X000001")
        one = filing(db, who, "PTR", datetime(2025, 5, 1), "X-1")
        trade(db, one)
        trade(db, one)

        assert len(member_transactions(db, who.id)) == 2

    def test_a_third_identical_row_in_that_filing_is_kept_too(self, db):
        who = member(db, bioguide="X000002")
        one = filing(db, who, "PTR", datetime(2025, 5, 1), "X-2")
        for _ in range(3):
            trade(db, one)

        assert len(member_transactions(db, who.id)) == 3

    def test_two_in_one_filing_and_a_restatement_of_both(self, db):
        """The combined case: the original holds two real trades, the amendment
        restates both. Four rows in, two trades out."""
        who = member(db, bioguide="X000003")
        original = filing(db, who, "Annual", datetime(2025, 5, 1), "X-3a")
        amendment = filing(db, who, "Annual (Amendment 1)", datetime(2025, 9, 1), "X-3b")
        trade(db, original)
        trade(db, original)
        trade(db, amendment)
        trade(db, amendment)

        kept = member_transactions(db, who.id)

        assert len(kept) == 2
        assert {t.disclosure_id for t in kept} == {original.id}


class TestOnlyGenuinelyIdenticalRowsCollapse:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("description", "A Different Fund"),
            ("when", datetime(2024, 4, 23)),
            ("kind", TransactionType.PURCHASE),
            ("low", "15001"),
            ("high", "50000"),
            ("owner", "Spouse"),
            ("ticker", "AAPL"),
        ],
    )
    def test_a_row_differing_in_any_compared_field_is_a_separate_trade(self, db, field, value):
        who = member(db, bioguide="X000004")
        original = filing(db, who, "Annual", datetime(2025, 5, 1), "X-4a")
        amendment = filing(db, who, "Annual (Amendment 1)", datetime(2025, 9, 1), "X-4b")
        trade(db, original)
        trade(db, amendment, **{field: value})

        assert len(member_transactions(db, who.id)) == 2

    def test_amount_scale_does_not_split_a_restatement(self, db):
        """`Numeric` comes back as Decimal, and a restated row can carry
        "1001" or "1001.00". Comparing str() on the raw value would treat those
        as different trades and defeat the whole rule."""
        who = member(db, bioguide="X000005")
        original = filing(db, who, "Annual", datetime(2025, 5, 1), "X-5a")
        amendment = filing(db, who, "Annual (Amendment 1)", datetime(2025, 9, 1), "X-5b")
        trade(db, original, low="1001", high="15000")
        trade(db, amendment, low="1001.00", high="15000.00")

        assert len(member_transactions(db, who.id)) == 1

    def test_the_key_ignores_which_filing_carried_the_row(self, db, tillis):
        who, original, amendment = tillis
        rows = {t.disclosure_id: t for t in db.query(Transaction).all()}
        from_original = next(t for t in rows.values() if t.disclosure_id == original.id)
        from_amendment = next(t for t in rows.values() if t.disclosure_id == amendment.id)

        # Same description picked from each side.
        a = next(
            t
            for t in db.query(Transaction).filter(Transaction.disclosure_id == original.id)
            if t.description == from_amendment.description
        )
        assert content_key(a) == content_key(from_amendment)
        assert from_original is not None


class TestOtherMembersAreNotMerged:
    def test_two_members_with_identical_trades_keep_both(self, db):
        """The rule is per member. Two senators selling the same fund on the
        same day in the same band is a coincidence, not a restatement."""
        one = member(db, bioguide="A000001", last="Alpha")
        two = member(db, bioguide="B000001", last="Beta")
        trade(db, filing(db, one, "PTR", datetime(2025, 5, 1), "A-1"))
        trade(db, filing(db, two, "PTR", datetime(2025, 5, 1), "B-1"))

        assert len(member_transactions(db, one.id)) == 1
        assert len(member_transactions(db, two.id)) == 1

        grouped = transactions_by_member(db)
        assert len(grouped[one.id]) == 1
        assert len(grouped[two.id]) == 1


class TestTheBatchFormAgreesWithTheSingle:
    def test_same_answer_either_way(self, db, tillis):
        who, _, _ = tillis

        assert [t.id for t in transactions_by_member(db)[who.id]] == [
            t.id for t in member_transactions(db, who.id)
        ]

    def test_it_can_be_scoped_to_some_members(self, db, tillis):
        who, _, _ = tillis
        other = member(db, bioguide="C000001", last="Gamma")
        trade(db, filing(db, other, "PTR", datetime(2025, 5, 1), "C-1"))

        grouped = transactions_by_member(db, [who.id])

        assert set(grouped) == {who.id}


class TestOrderingIsStable:
    def test_rows_come_back_oldest_first(self, db):
        who = member(db, bioguide="X000006")
        one = filing(db, who, "PTR", datetime(2025, 5, 1), "X-6")
        trade(db, one, when=datetime(2024, 3, 1), description="Later")
        trade(db, one, when=datetime(2024, 1, 1), description="Earlier")

        kept = member_transactions(db, who.id)

        assert [t.description for t in kept] == ["Earlier", "Later"]

    def test_a_filing_missing_from_the_date_map_never_wins(self, db):
        """`filing_date` is NOT NULL in the schema, so an undated disclosure
        cannot exist -- but `drop_restatements` takes the date map as an
        argument, and a caller can hand it one that omits a disclosure. The
        unknown date must sort LAST, so the choice never depends on row order."""
        who = member(db, bioguide="X000007")
        known = filing(db, who, "Annual", datetime(2025, 5, 1), "X-7a")
        unknown = filing(db, who, "Annual (Amendment 1)", datetime(2024, 1, 1), "X-7b")
        for i in range(3):
            trade(db, unknown, description=f"Holding {i}")
            trade(db, known, description=f"Holding {i}")
        rows = db.query(Transaction).all()

        # Deliberately omit the amendment from the date map, though its stored
        # date is EARLIER: an absent date must sort LAST, so the filing whose
        # date we hold is the one kept regardless of row order.
        kept = drop_restatements(rows, {known.id: known.filing_date})

        assert len(kept) == 3
        assert {t.disclosure_id for t in kept} == {known.id}


class TestEmptyInputs:
    def test_no_transactions_is_not_an_error(self, db):
        who = member(db, bioguide="X000008")

        assert member_transactions(db, who.id) == []
        assert drop_restatements([], {}) == []

    def test_scoping_to_no_members_returns_nothing(self, db, tillis):
        assert transactions_by_member(db, []) == {}


class TestConsecutiveTradesNeedsATimeWindow:
    """It published "within a short period" and read no dates at all.

    `_check_consecutive_trades` never touched `transaction_date`; the only
    temporal input was the caller's ORDER BY, which fixes order and bounds
    nothing. Live consequences, all published:

        Sean Casten     11 consecutive trades over 1,131 days  (his whole history)
        Doris Matsui    18 over 866 days
        Jon Ossoff      16 over 456 days
        Eleanor Norton   9 over 558 days, in 7 different filings

    Without a window this measures which direction a member mostly traded, not a
    pattern. The window is the STOCK Act PTR deadline the project already
    asserts, so the constant is one the site already explains.
    """

    def _detector(self):
        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        return ExtendedAnomalyDetector()

    def _run(self, db, days_apart, count=11, kind=TransactionType.PURCHASE):
        who = member(db, bioguide=f"W{days_apart:06d}", last="Window")
        one = filing(db, who, "PTR", datetime(2025, 1, 1), f"W-{days_apart}")
        for i in range(count):
            trade(
                db,
                one,
                description=f"Holding {i}",
                when=datetime(2024, 1, 1) + timedelta(days=i * days_apart),
                kind=kind,
            )
        return self._detector()._same_direction_run(member_transactions(db, who.id))

    def test_castens_three_year_run_does_not_fire(self, db):
        """1,131 days over 11 purchases is ~113 days between trades."""
        assert self._run(db, days_apart=113) is None

    def test_a_genuine_burst_still_fires(self, db):
        result = self._run(db, days_apart=2)

        assert result is not None
        assert result.length == 11
        assert result.span_days == 20
        assert result.days == 11, "eleven trades on eleven distinct dates"

    def test_the_span_is_reported_not_the_phrase(self, db):
        span = self._run(db, days_apart=2).span_days

        assert span == 20, "the published sentence states this, not 'a short period'"

    def test_a_run_exactly_on_the_window_fires(self, db):
        from src.analysis.extended_anomaly_detector import CONSECUTIVE_TRADE_WINDOW_DAYS

        # 5 trades spanning exactly the window.
        result = self._run(db, days_apart=CONSECUTIVE_TRADE_WINDOW_DAYS // 4, count=5)

        assert result is not None
        assert result.span_days <= CONSECUTIVE_TRADE_WINDOW_DAYS

    def test_fewer_than_the_threshold_never_fires(self, db):
        assert self._run(db, days_apart=1, count=4) is None

    def test_the_window_is_the_stock_act_deadline(self):
        from src.analysis.extended_anomaly_detector import CONSECUTIVE_TRADE_WINDOW_DAYS
        from src.analysis.trade_analyzer import TradeAnalyzer

        assert CONSECUTIVE_TRADE_WINDOW_DAYS == TradeAnalyzer().ptr_deadline_days == 45

    def test_a_long_history_still_finds_a_burst_inside_it(self, db):
        """The run is the longest window-fitting stretch, not the whole
        history: five trades in a week, then years of scattered ones."""
        who = member(db, bioguide="B000009", last="Burst")
        one = filing(db, who, "PTR", datetime(2025, 1, 1), "B-9")
        for i in range(6):
            trade(db, one, description=f"Burst {i}", when=datetime(2024, 1, 1) + timedelta(days=i))
        for i in range(4):
            trade(
                db,
                one,
                description=f"Scattered {i}",
                when=datetime(2024, 6, 1) + timedelta(days=i * 200),
            )

        result = self._detector()._same_direction_run(member_transactions(db, who.id))

        assert result is not None
        assert result.length == 6
        assert result.span_days == 5

    def test_a_direction_change_breaks_the_run(self, db):
        who = member(db, bioguide="D000009", last="Direction")
        one = filing(db, who, "PTR", datetime(2025, 1, 1), "D-9")
        for i in range(4):
            trade(
                db,
                one,
                description=f"Buy {i}",
                when=datetime(2024, 1, 1) + timedelta(days=i),
                kind=TransactionType.PURCHASE,
            )
        trade(
            db,
            one,
            description="Sell",
            when=datetime(2024, 1, 5),
            kind=TransactionType.SALE,
        )

        assert self._detector()._same_direction_run(member_transactions(db, who.id)) is None


class TestTheEvidenceBarProtectsRealTrades:
    """The decision is between FILINGS, not between rows, and this is why.

    Judging row by row deleted disclosed trades: the compliance fixtures build
    four separate PTRs that each report one same-day purchase of the same stock
    in the same band, and a row-level rule collapsed all four into one. Below
    the threshold, nothing is dropped.
    """

    def _pair(self, db, rows, later_type="Annual", bioguide="E000001"):
        who = member(db, bioguide=bioguide, last="Evidence")
        first = filing(db, who, "Annual", datetime(2025, 1, 1), f"{bioguide}-a")
        second = filing(db, who, later_type, datetime(2025, 6, 1), f"{bioguide}-b")
        for i in range(rows):
            trade(db, first, description=f"Holding {i}")
            trade(db, second, description=f"Holding {i}")
        return member_transactions(db, who.id)

    @pytest.mark.parametrize("rows", [1, 2])
    def test_below_the_bar_nothing_is_dropped(self, db, rows):
        assert len(self._pair(db, rows, bioguide=f"E00000{rows}")) == rows * 2

    @pytest.mark.parametrize("rows", [3, 7])
    def test_at_or_above_the_bar_the_later_copies_go(self, db, rows):
        assert len(self._pair(db, rows, bioguide=f"E00001{rows}")) == rows

    def test_the_bar_is_where_the_module_says_it_is(self):
        from src.analysis.restatements import MIN_SHARED_ROWS_FOR_RESTATEMENT

        assert MIN_SHARED_ROWS_FOR_RESTATEMENT == 3

    def test_an_amendment_label_needs_no_corroboration(self, db):
        """The Senate writes "(Amendment 1)" and preserves it verbatim. Where
        the label exists it is decisive, so a single restated row collapses."""
        kept = self._pair(
            db, 1, later_type="Annual Report for CY 2024 (Amendment 1)", bioguide="E000020"
        )

        assert len(kept) == 1

    def test_the_label_is_matched_case_insensitively(self, db):
        kept = self._pair(db, 1, later_type="ANNUAL REPORT (AMENDED)", bioguide="E000021")

        assert len(kept) == 1

    def test_four_separate_ptrs_of_the_same_trade_all_survive(self, db):
        """The exact shape that broke `tests/test_compliance.py`: four PTRs, one
        identical same-day purchase each, no amendment label. Four disclosed
        trades, and the compliance rate depends on all four being counted."""
        who = member(db, bioguide="E000030", last="Compliance")
        for n, lag in enumerate((10, 20, 75, 105)):
            one = filing(db, who, "PTR", datetime(2024, 1, 1) + timedelta(days=lag), f"C-{n}")
            trade(db, one, when=datetime(2024, 1, 1))

        assert len(member_transactions(db, who.id)) == 4


class TestAnAmendmentThatAddsATrade:
    def test_the_added_trade_survives(self, db):
        """Tillis's amendment carries 33 rows against the original's 32. The
        extra row is a trade the original omitted and must not be dropped with
        the restated ones."""
        who = member(db, bioguide="A000099", last="Added")
        original = filing(db, who, "Annual", datetime(2025, 1, 1), "A-99a")
        amendment = filing(db, who, "Annual (Amendment 1)", datetime(2025, 6, 1), "A-99b")
        for i in range(4):
            trade(db, original, description=f"Holding {i}")
            trade(db, amendment, description=f"Holding {i}")
        trade(db, amendment, description="Only in the amendment")

        kept = member_transactions(db, who.id)

        assert len(kept) == 5
        assert "Only in the amendment" in {t.description for t in kept}

    def test_a_trade_the_original_reported_once_and_the_amendment_twice(self, db):
        """Drop only as many copies as the earlier filing carried. The second
        copy in the amendment is a trade the original omitted, not a repeat."""
        who = member(db, bioguide="A000098", last="Twice")
        original = filing(db, who, "Annual", datetime(2025, 1, 1), "A-98a")
        amendment = filing(db, who, "Annual (Amendment 1)", datetime(2025, 6, 1), "A-98b")
        for i in range(3):
            trade(db, original, description=f"Holding {i}")
            trade(db, amendment, description=f"Holding {i}")
        trade(db, amendment, description="Holding 0")

        kept = member_transactions(db, who.id)

        assert len(kept) == 4
        assert sum(1 for t in kept if t.description == "Holding 0") == 2
