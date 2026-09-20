"""Three counts that did not say what they were counts of.

**`committee_jurisdiction_conflict` called its denominator "disclosed
trades".** It counts only the member's own rows, correctly, per #99 -- and
then labelled the result with a word meaning everything the filing discloses.
Rep. Hill's filings disclose 16 transactions; the finding said 13. The
denominator is load-bearing here, not cosmetic: 3 of 13 is 23.1% and clears
the 20% gate, 3 of 16 is 18.8% and does not. A reader with the filing open had
no way to get from 16 to 13.

**`high_trading_frequency` said "High trading activity" without saying how
concentrated.** 19 of the corpus's 132 findings describe a month whose every
row shares ONE date -- Rep. Keating's fifteen are all 11 September 2024, Sen.
Tuberville's sixteen all 15 April 2025. That is one reallocation. The same
argument #103 made for `trade_clustering` applies: a PTR records a date and no
time of day, so a single date is a batch, not a month of decisions.

**And the clause saying what was excluded existed three times.** #104 wrote it
for a month, #109 copied it for a filing, and `committee_conflicts` needed a
third for a member's whole record. Three copies of one sentence about the same
rows is three chances to drift, so the clause lives in `attribution`, where
`owner_breakdown`'s docstring already states the rule, and the detectors supply
only the scope.
"""

from __future__ import annotations

import ast
from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.attribution import excluded_clause, whose_they_are
from src.analysis.committee_conflicts import detect_committee_jurisdiction_conflicts
from src.analysis.trade_analyzer import TradeAnalyzer, _over_how_many_days
from src.db.models import Chamber, Disclosure, Member, Party, TransactionType
from tests.test_committee_conflicts import _assign, _trade


@pytest.fixture
def seated(db_session):
    """A senator on Armed Services, with one PTR to hang rows off."""
    m = Member(
        bioguide_id="WC00001",
        first_name="Count",
        last_name="Covers",
        chamber=Chamber.SENATE,
        party=Party.REPUBLICAN,
        state="VA",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    d = Disclosure(
        member_id=m.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 4, 1),
        document_id="WC-1",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return m, d


class _Txn:
    def __init__(self, when, owner="Self", tid=1, kind=TransactionType.PURCHASE):
        self.transaction_date = when
        self.transaction_type = kind
        self.owner = owner
        self.id = tid
        self.disclosure_id = 1
        self.ticker = "AAPL"
        self.description = "Apple Inc. - Common Stock"
        self.amount_min = Decimal("1001")
        self.amount_max = Decimal("15000")


class TestOneClauseNotThree:
    def test_the_scope_is_the_only_thing_a_detector_supplies(self):
        rows = [_Txn(datetime(2025, 3, 4), owner="Spouse", tid=i) for i in range(25)]
        for lead in (
            "The filings covering that month also report",
            "The same filing also reports",
            "This member's filings also report",
        ):
            clause = excluded_clause(rows, lead)
            assert clause.startswith(f" {lead} 25 transaction(s)")
            assert clause.endswith("belonging to their spouse, which this count excludes.")

    def test_nothing_excluded_says_nothing(self):
        assert excluded_clause([], "anything") == ""

    def test_both_household_kinds_are_named(self):
        assert whose_they_are({"Spouse": 2, "Dependent Child": 1}) == (
            "their spouse and a dependent child"
        )
        assert whose_they_are({"Dependent Child": 1}) == "a dependent child"
        assert whose_they_are({}) == "someone other than the member"

    def test_the_detectors_do_not_keep_their_own_copies(self):
        """Structural: three copies of one sentence is three chances to drift."""
        for module in (
            "src/analysis/trade_analyzer.py",
            "src/analysis/committee_conflicts.py",
        ):
            source = open(module).read()
            assert "which this count excludes" not in source, (
                f"{module} writes the excluded clause itself instead of "
                "calling attribution.excluded_clause"
            )


class TestAMonthOnOneDay:
    def test_a_single_date_is_named_and_called_a_batch(self):
        rows = [_Txn(datetime(2024, 9, 11), tid=i) for i in range(15)]
        clause = _over_how_many_days(rows)
        assert "All of them are dated 11 September 2024" in clause
        assert "one day's batch" in clause
        assert "no time of day" in clause

    def test_several_dates_get_one_short_sentence(self):
        rows = [_Txn(datetime(2024, 9, d), tid=d) for d in (2, 9, 16, 23)]
        assert _over_how_many_days(rows) == " They fall on 4 days of trading."

    def test_two_rows_on_one_date_are_still_one_day(self):
        rows = [_Txn(datetime(2024, 9, 11), tid=1), _Txn(datetime(2024, 9, 11), tid=2)]
        assert "All of them are dated" in _over_how_many_days(rows)

    def test_undated_rows_produce_no_claim(self):
        assert _over_how_many_days([_Txn(None)]) == ""
        assert _over_how_many_days([]) == ""

    def test_the_published_finding_carries_it(self):
        rows = [_Txn(datetime(2024, 9, 11), tid=i) for i in range(15)]
        found = TradeAnalyzer()._check_trading_frequency(rows, member_id=1, member=None)
        assert len(found) == 1
        assert "one day's batch" in found[0]["description"]
        assert "above the threshold of 10 per month" in found[0]["description"]
        # The claim itself is untouched.
        assert found[0]["title"] == "High trading activity: 15 trades in September 2024"
        assert int(found[0]["computed_value"]) == 15


class TestTheCommitteeDenominatorIsNamed:
    def test_the_label_disclosed_is_gone_from_the_detector(self):
        source = open("src/analysis/committee_conflicts.py").read()
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        emittable = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]
        assert not [s for s in emittable if "disclosed trades" in s]
        assert [s for s in emittable if "trades attributed to this" in s]

    def test_the_household_figure_is_carried_for_the_api(self):
        source = open("src/analysis/committee_conflicts.py").read()
        assert '"household_trades": len(household)' in source

    def test_the_filter_itself_is_unchanged(self):
        """Naming what was excluded must not become counting it."""
        source = open("src/analysis/committee_conflicts.py").read()
        assert "trades_the_member_holds(household)" in source


class TestWhatTheCommitteeFindingPublishes:
    """End to end, because the label is what reached readers.

    Rep. Hill's shape, reproduced: four defense rows the member holds and three
    a spouse holds, so the denominator the finding names must be four and the
    sentence must say where the other three went.
    """

    def _finding(self, db, seated):
        who, filing = seated
        _assign(db, who, "SSAS")
        for ticker in ("LMT", "RTX", "NOC", "GD"):
            _trade(db, filing, ticker)
        found = detect_committee_jurisdiction_conflicts(db)
        return found[0] if found else None

    def test_the_denominator_is_named_not_called_disclosed(self, db_session, seated):
        found = self._finding(db_session, seated)
        assert "of 4 trades attributed to this member" in found["description"]
        assert "disclosed trades" not in found["description"]

    def test_the_household_rows_are_named_so_the_filing_reconciles(self, db_session, seated):
        for ticker in ("HON", "BA", "TXT"):
            _trade(db_session, seated[1], ticker, owner="Spouse")
        found = self._finding(db_session, seated)
        assert "4 trades attributed to this member" in found["description"]
        assert "3 transaction(s) belonging to their spouse" in found["description"]
        assert found["household_trades"] == 7
        assert found["total_trades"] == 4

    def test_a_record_with_nothing_excluded_gains_no_clause(self, db_session, seated):
        found = self._finding(db_session, seated)
        assert "excludes" not in found["description"]
        assert found["household_trades"] == found["total_trades"]


@pytest.mark.parametrize(
    "needle",
    [
        ("high_trading_frequency", "description", "which exceeds the threshold of"),
        ("committee_jurisdiction_conflict", "description", "disclosed trades"),
    ],
)
def test_the_published_sentences_are_taken_down(needle):
    """Both are keyed by title, and neither title changed.

    `persist_anomalies` only ever inserts, so an unchanged identity means the
    old description is served for ever unless the purge names it.
    """
    from src.cli import _SUPERSEDED_WORDING

    assert needle in _SUPERSEDED_WORDING
