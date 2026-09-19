"""Found by auditing what this project publishes against the filings it
publishes about. Two defects, and the second one was mine.

**A month was not a month.** `_check_trading_frequency` grouped by DISCLOSURE
first and month second, so a member who reports one month across two PTRs
produced two findings, each counting part of it, each titled "in <Month Year>".
Sen. Boozman filed 17 August 2025 trades and 21 more on the same day: two
findings saying 17 and 21 about a month containing 38. On a 10,594-row corpus
**128 member-months are split across more than one filing, carrying 2,668
rows** -- a quarter of the corpus.

**The count is the member's own, and the sentence did not say so.** #99 stopped
this counting a spouse's trades, which was right, and left the wording alone,
which was not. Rep. Byron Donalds' March 2025 filing prints 48 transactions --
23 his, 25 his spouse's -- and the finding read

    23 transactions were disclosed in March 2025

against a document showing 48. The number is correct and unverifiable, which is
the "right number, wrong noun" defect #100 fixed in this same sentence,
reintroduced by the fix for #99 one commit earlier.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import TradeAnalyzer
from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType


def _member(db, bioguide):
    m = Member(
        bioguide_id=bioguide,
        first_name="Month",
        last_name="Ly",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="FL",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _ptr(db, member, doc):
    d = Disclosure(
        member_id=member.id,
        filing_year=2025,
        filing_type="PTR",
        filing_date=datetime(2025, 4, 10),
        document_id=doc,
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _rows(db, disclosure, n, owner, start=datetime(2025, 3, 3)):
    """Distinct assets per filing, on purpose.

    Identical rows in two filings are what `restatements.py` reads as an
    amendment restating an original, and it drops the later copy. A fixture
    that reuses asset names across filings tests that guard instead of this
    one, and reports zero findings for a reason that has nothing to do with
    month grouping.
    """
    tag = disclosure.document_id.replace("-", "")
    for i in range(n):
        db.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=start + timedelta(days=i % 20),
                transaction_type=TransactionType.PURCHASE,
                description=f"{tag} Holding {owner}{i} - Common Stock ({tag}{i}) [ST]",
                ticker=f"{tag[:2]}{i:03d}"[:8],
                amount_min=Decimal("1001") + i,
                amount_max=Decimal("15000") + i,
                owner=owner,
            )
        )
    db.commit()


def _frequency(db, member):
    return [
        a
        for a in TradeAnalyzer().analyze_member(db, member.id)
        if a["anomaly_type"] == "high_trading_frequency"
    ]


class TestAMonthSpanningFilings:
    def test_boozmans_shape_is_one_finding_for_the_whole_month(self, db_session):
        member = _member(db_session, "MO00001")
        _rows(db_session, _ptr(db_session, member, "M-1"), 7, "Self")
        _rows(db_session, _ptr(db_session, member, "M-2"), 6, "Self")

        found = _frequency(db_session, member)

        assert len(found) == 1, "two filings covering one month are one month"
        assert int(found[0]["computed_value"]) == 13
        assert "13 trades in March 2025" in found[0]["title"]

    def test_neither_filing_alone_would_clear_the_threshold(self, db_session):
        """The half that the old grouping got backwards: split a real month
        across enough filings and it disappears entirely."""
        member = _member(db_session, "MO00002")
        for i in range(3):
            _rows(db_session, _ptr(db_session, member, f"S-{i}"), 5, "Self")

        found = _frequency(db_session, member)

        assert len(found) == 1
        assert int(found[0]["computed_value"]) == 15

    def test_separate_months_stay_separate(self, db_session):
        member = _member(db_session, "MO00003")
        filing = _ptr(db_session, member, "M-3")
        _rows(db_session, filing, 11, "Self", start=datetime(2025, 3, 3))
        _rows(db_session, filing, 12, "Self", start=datetime(2025, 5, 5))

        months = sorted(a["title"] for a in _frequency(db_session, member))

        assert len(months) == 2
        assert "March 2025" in months[0] and "May 2025" in months[1]

    def test_the_finding_links_to_the_filing_carrying_most_of_the_month(self, db_session):
        member = _member(db_session, "MO00004")
        small = _ptr(db_session, member, "M-small")
        big = _ptr(db_session, member, "M-big")
        _rows(db_session, small, 4, "Self")
        _rows(db_session, big, 9, "Self")

        found = _frequency(db_session, member)

        assert found[0]["disclosure_id"] == big.id


class TestTheCountSaysWhoseItIs:
    def test_donalds_shape_discloses_the_rows_it_excludes(self, db_session):
        # 23 his, 25 his spouse's, in a filing printing 48.
        member = _member(db_session, "MO00005")
        filing = _ptr(db_session, member, "M-5")
        _rows(db_session, filing, 23, "Self")
        _rows(db_session, filing, 25, "Spouse")

        found = _frequency(db_session, member)
        assert len(found) == 1
        text = found[0]["description"]

        assert int(found[0]["computed_value"]) == 23, "the count is still the member's own"
        assert "attributed to this member" in text
        assert "25 transaction(s) belonging to their spouse" in text
        assert "excludes" in text

    def test_a_dependent_child_is_named_too(self, db_session):
        member = _member(db_session, "MO00006")
        filing = _ptr(db_session, member, "M-6")
        _rows(db_session, filing, 12, "Self")
        _rows(db_session, filing, 5, "Dependent Child")

        text = _frequency(db_session, member)[0]["description"]

        assert "a dependent child" in text

    def test_nothing_is_claimed_when_nothing_was_excluded(self, db_session):
        """A member with no household rows gets no dangling clause."""
        member = _member(db_session, "MO00007")
        _rows(db_session, _ptr(db_session, member, "M-7"), 14, "Self")

        text = _frequency(db_session, member)[0]["description"]

        assert "excludes" not in text
        assert "belonging to" not in text

    def test_the_sentence_no_longer_claims_to_count_the_filing(self, db_session):
        """ "disclosed in <month>" is what made a correct number unverifiable."""
        member = _member(db_session, "MO00008")
        filing = _ptr(db_session, member, "M-8")
        _rows(db_session, filing, 11, "Self")
        _rows(db_session, filing, 20, "Spouse")

        text = _frequency(db_session, member)[0]["description"]

        assert "transactions were disclosed in" not in text


@pytest.mark.parametrize("owner", ["Spouse", "Dependent Child"])
def test_a_household_only_month_produces_nothing(db_session, owner):
    """The guard from #99, restated here because this file changed the shape of
    the code that enforces it."""
    member = _member(db_session, f"MO1{owner[:3]}")
    _rows(db_session, _ptr(db_session, member, f"M-{owner[:3]}"), 20, owner)

    assert _frequency(db_session, member) == []
