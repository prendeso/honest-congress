"""A detector that counts a member's trades must not count their household's.

A financial disclosure is a household document. The law makes a member report
their spouse's and dependent children's transactions; it does not make those
the member's trades. The House PTR form says which is which in its own column
-- `SP`, `JT`, `DC`, blank for the filer -- and #98 taught the parser to read
it.

Nothing then acted on it. `owner` appeared in `src/analysis` in exactly two
roles, a restatement content-key component and a projection label, and no
detector filtered on it. So every "Member made N trades" finding published a
household total under one name.

Measured over a 10,594-transaction corpus of real House PTRs, after #98:

    Self 4,550 | Spouse 3,094 | Joint 2,527 | Dependent Child 423

    57.1% of rows are not the filer's own.

The published consequences, each verified against the primary document:

* Sen. Hagerty's four "unusual volume spikes" are four rows stored CORRECTLY as
  `Dependent Child`. #98 changes nothing about them -- nothing was misread.
  They were counted as his because no code asked whose they were.
* Rep. McClain Delaney was flagged for "17 trades" of which she made none.

`Joint` counts and `Spouse`/`Dependent Child` do not: `JT` means held jointly
by the filer and spouse, so the member is a party to it. Drawn conservatively
on purpose -- an accusation loses only what the member has no stake in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.attribution import (
    held_by_member,
    owner_breakdown,
    trades_the_member_holds,
)
from src.analysis.extended_anomaly_detector import (
    MIN_CONSECUTIVE_TRADES,
    ExtendedAnomalyDetector,
)
from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType


class _Row:
    """The duck type every caller passes: anything with an `owner`."""

    def __init__(self, owner):
        self.owner = owner


class TestWhoTheRuleCounts:
    def test_the_filer_and_a_joint_holding_are_the_members(self):
        assert held_by_member(_Row("Self"))
        assert held_by_member(_Row("Joint"))

    def test_a_spouse_and_a_dependent_child_are_not(self):
        assert not held_by_member(_Row("Spouse"))
        assert not held_by_member(_Row("Dependent Child"))

    def test_a_blank_owner_is_the_filer(self):
        # The House form leaves the owner column empty for the filer, so blank
        # is the document's way of saying "mine" -- not an unread value.
        assert held_by_member(_Row(""))
        assert held_by_member(_Row(None))
        assert held_by_member(_Row("   "))

    def test_an_owner_nobody_taught_this_module_is_not_the_members(self):
        # The conservative direction: an owner string outside the vocabulary
        # `_normalize_owner` emits is not grounds for naming someone.
        assert not held_by_member(_Row("Trust"))
        assert not held_by_member(_Row("SP"))

    def test_the_filter_keeps_order(self):
        rows = [_Row("Self"), _Row("Spouse"), _Row("Joint"), _Row("Dependent Child")]
        kept = trades_the_member_holds(rows)
        assert [r.owner for r in kept] == ["Self", "Joint"]

    def test_the_breakdown_discloses_what_was_dropped(self):
        rows = [_Row("Self"), _Row("Spouse"), _Row("Spouse"), _Row(None)]
        assert owner_breakdown(rows) == {"Self": 2, "Spouse": 2}


# ---------------- end to end ----------------


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="Att",
        last_name="Ribution",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="NY",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _ptr(db, member, doc_id):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id=doc_id,
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _run_of_purchases(db, disclosure, owner, count=MIN_CONSECUTIVE_TRADES):
    """A streak long enough to clear `trade_clustering` on its own."""
    start = datetime(2024, 3, 1)
    for i in range(count):
        db.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=start + timedelta(days=i),
                transaction_type=TransactionType.PURCHASE,
                description=f"Holding {i} - Common Stock",
                ticker=f"AA{i}",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
                owner=owner,
            )
        )
    db.commit()


def _clustering_findings(db, member):
    return [
        a
        for a in ExtendedAnomalyDetector().detect_trade_timing_anomalies(db)
        if a["anomaly_type"] == "trade_clustering" and a["member_id"] == member.id
    ]


class TestTheDetectorAsksWhoseTradeItIs:
    def test_a_spouses_streak_is_not_published_as_the_members(self, db_session):
        member = _member(db_session, "SP00001")
        _run_of_purchases(db_session, _ptr(db_session, member, "D-SP-1"), "Spouse")

        assert _clustering_findings(db_session, member) == []

    def test_a_dependent_childs_streak_is_not_either(self, db_session):
        # Hagerty's case exactly: the rows are stored correctly, and were
        # counted anyway.
        member = _member(db_session, "DC00001")
        _run_of_purchases(db_session, _ptr(db_session, member, "D-DC-1"), "Dependent Child")

        assert _clustering_findings(db_session, member) == []

    def test_the_members_own_streak_still_is(self, db_session):
        # The other half of the guard: this must not simply stop detecting.
        member = _member(db_session, "SE00001")
        _run_of_purchases(db_session, _ptr(db_session, member, "D-SE-1"), "Self")

        found = _clustering_findings(db_session, member)
        assert len(found) == 1
        assert found[0]["count"] == MIN_CONSECUTIVE_TRADES

    def test_a_joint_holding_still_counts(self, db_session):
        member = _member(db_session, "JT00001")
        _run_of_purchases(db_session, _ptr(db_session, member, "D-JT-1"), "Joint")

        assert len(_clustering_findings(db_session, member)) == 1

    def test_a_household_that_only_clears_the_bar_together_does_not_clear_it(self, db_session):
        # The shape that produced "18 stock trades" for a member who made
        # five: enough rows in total, not enough that are theirs.
        member = _member(db_session, "MX00001")
        disclosure = _ptr(db_session, member, "D-MX-1")
        _run_of_purchases(db_session, disclosure, "Self", count=MIN_CONSECUTIVE_TRADES - 2)
        _run_of_purchases(db_session, disclosure, "Spouse", count=MIN_CONSECUTIVE_TRADES)

        assert _clustering_findings(db_session, member) == []


class TestWhereTheFilterMustNotReach:
    """Two detectors must keep counting the whole household, deliberately."""

    def test_late_filing_still_scores_a_spouses_trade(self, db_session):
        # The STOCK Act deadline is the MEMBER's obligation for every
        # reportable trade, their spouse's included. Filtering here would erase
        # real late filings -- and late_filing is the one detector that
        # survived the adversarial audit.
        from src.analysis.trade_analyzer import TradeAnalyzer

        member = _member(db_session, "LF00001")
        disclosure = _ptr(db_session, member, "D-LF-1")
        db_session.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=datetime(2023, 1, 5),
                transaction_type=TransactionType.SALE,
                description="Very Late Holdings - Common Stock",
                ticker="LATE",
                amount_min=Decimal("100001"),
                amount_max=Decimal("250000"),
                owner="Spouse",
            )
        )
        db_session.commit()

        # Through `analyze_member`, NOT `_check_late_filings` directly. Calling
        # the helper is what let the first version of this fix pass its own
        # test while erasing 85 real late filings from a 596-finding corpus:
        # `analyze_member` returned early when the member's OWN rows were
        # empty, so late filing never ran for a member whose disclosed trades
        # were all their spouse's -- which is this member exactly.
        found = TradeAnalyzer().analyze_member(db_session, member.id)
        late = [a for a in found if a["anomaly_type"] == "late_filing"]
        assert len(late) == 1, "a spouse's trade filed late is still the member's late filing"

    def test_a_member_whose_every_row_is_a_spouses_is_still_analysed(self, db_session):
        # The guard on the early return itself. The household list decides
        # whether there is anything to look at; the member's own list decides
        # what may be attributed to them.
        from src.analysis.trade_analyzer import TradeAnalyzer

        member = _member(db_session, "LF00002")
        disclosure = _ptr(db_session, member, "D-LF-2")
        db_session.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=datetime(2023, 2, 2),
                transaction_type=TransactionType.SALE,
                description="Spouse Only Holdings - Common Stock",
                ticker="SPON",
                amount_min=Decimal("100001"),
                amount_max=Decimal("250000"),
                owner="Spouse",
            )
        )
        db_session.commit()

        found = TradeAnalyzer().analyze_member(db_session, member.id)
        assert [a["anomaly_type"] for a in found] == ["late_filing"]

    def test_opacity_still_counts_the_whole_filing(self, db_session):
        # Disclosure completeness is measured over everything the member had to
        # report, so the denominator keeps the household.
        import inspect

        import src.analysis.opacity as opacity

        source = inspect.getsource(opacity)
        assert "trades_the_member_holds" not in source
        assert "held_by_member" not in source


@pytest.mark.parametrize(
    "module",
    ["src.analysis.compliance", "src.analysis.opacity"],
)
def test_the_exclusions_are_pinned(module):
    """A later caller cannot quietly apply the filter where it must not go."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))
    assert "src.analysis.attribution" not in source, (
        f"{module} counts the household on purpose -- see attribution.py"
    )
