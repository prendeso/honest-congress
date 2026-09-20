"""The form prints the date the filer was notified, and nothing stored it.

Every House PTR prints a **Notification Date** beside the transaction date.
`ptr_parser` has read it since the golden tests were written
(`tests/test_ptr_parser_golden.py` asserts it), `Transaction` had no column for
it, and both `_store_ptr_data` call sites dropped it on the floor -- so
`compliance.py`'s own docstring said *"awareness dates are not disclosed"*
about a column printed on every form this project parses.

**It does not move the deadline, and a proposal to let it was rejected.** One
of the audit's fatal findings asked for a guard skipping any row notified
within 30 days of filing. That is wrong on the statute: 5 U.S.C. 13104(l)
requires a report within 30 days of notification *"but in no case later than 45
days after such transaction"*. A late notification can only SHORTEN a filer's
window, never extend it past 45 days -- so the guard would have suppressed real
violations, which is the one direction this project will not move. D7's clock
stands, and the column is published as context instead:

    Trade reported: purchase of Cleveland-Cliffs Inc. Common Stock, which the
    filing reports for their spouse. ... The filer reports being notified of
    it on 2024-08-20, 134 days after the trade -- itself past the 45-day cap,
    so no filing could have met the deadline. The deadline runs from the
    transaction regardless: the statute allows 30 days from notification but
    in no case more than 45 days from the trade.

That is material to a reader judging a named person, and it was sitting on the
document unread.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import PTR_HARD_CAP_DAYS, TradeAnalyzer, _when_they_were_told
from src.db.models import (
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


class _Txn:
    def __init__(self, traded, told):
        self.transaction_date = traded
        self.notification_date = told


class _Filing:
    def __init__(self, filed):
        self.filing_date = filed


class TestTheClause:
    def test_a_notification_after_the_cap_says_no_filing_could_have_been_timely(self):
        clause = _when_they_were_told(
            _Txn(datetime(2024, 4, 8), datetime(2024, 8, 20)), _Filing(datetime(2024, 9, 1))
        )
        assert "notified of it on 2024-08-20" in clause
        assert "134 days after the trade" in clause
        assert "past the 45-day cap" in clause
        assert "no filing could have met the deadline" in clause

    def test_a_notification_inside_the_cap_gives_the_gap_to_filing(self):
        clause = _when_they_were_told(
            _Txn(datetime(2024, 4, 8), datetime(2024, 4, 10)), _Filing(datetime(2024, 7, 1))
        )
        assert "notified of it on 2024-04-10" in clause
        assert "filed 82 days after that" in clause
        assert "past the" not in clause

    def test_the_statute_is_restated_either_way(self):
        for told in (datetime(2024, 4, 10), datetime(2024, 8, 20)):
            clause = _when_they_were_told(
                _Txn(datetime(2024, 4, 8), told), _Filing(datetime(2024, 9, 1))
            )
            assert "in no case more than 45 days from the trade" in clause

    def test_a_row_with_no_notification_date_makes_no_claim(self):
        # NULL is "not read yet" -- every row stored before the column existed,
        # and every Senate row. It must never read as "no notification".
        assert (
            _when_they_were_told(_Txn(datetime(2024, 4, 8), None), _Filing(datetime(2024, 9, 1)))
            == ""
        )

    def test_a_row_with_no_transaction_date_makes_no_claim(self):
        assert (
            _when_they_were_told(_Txn(None, datetime(2024, 8, 20)), _Filing(datetime(2024, 9, 1)))
            == ""
        )

    def test_an_object_without_the_attribute_at_all_is_tolerated(self):
        class Narrow:
            transaction_date = datetime(2024, 4, 8)

        assert _when_they_were_told(Narrow(), _Filing(datetime(2024, 9, 1))) == ""

    def test_the_cap_is_the_statutes_and_is_named_once(self):
        assert PTR_HARD_CAP_DAYS == 45


class TestAnImpossibleDateOnTheForm:
    """The form itself is sometimes wrong, and the parser is not.

    Rep. Jefferson Shreve's PTR 20029038 prints `03/28/1935` on 15 rows whose
    siblings on the same page read `03/28/2025`:

        American International Group, Inc.  P 03/13/2025 03/28/1935 $15,001 -
        Caterpillar Inc. Common Stock (CAT) S 03/13/2025 03/28/2025 $15,001 -

    Without a guard this clause publishes "notified 32,858 days before the
    trade" beside a named member. **38 of the corpus's 9,263 dated rows (0.4%)
    carry a notification date before the transaction**, across 13 filings --
    from a one-day slip to Rep. Shreve's ninety years -- and each was read back
    off the PDF: the parser is right and the document is wrong.

    So the clause reports what the form says and infers nothing from it. That
    is the same direction #105 took on the reconciliation: state the document,
    never invent around it.
    """

    def test_the_ninety_year_typo_is_not_turned_into_a_gap(self):
        clause = _when_they_were_told(
            _Txn(datetime(2025, 3, 13), datetime(1935, 3, 28)), _Filing(datetime(2025, 5, 1))
        )
        assert "notification date of 1935-03-28" in clause
        assert "before the transaction it reports" in clause
        assert "nothing is inferred from it here" in clause
        assert "32858" not in clause and "-32858" not in clause

    @pytest.mark.parametrize(
        "traded,told",
        [
            (datetime(2025, 2, 26), datetime(2025, 2, 25)),  # one day, 20027891
            (datetime(2025, 8, 19), datetime(2025, 8, 17)),  # two days, 20032061
            (datetime(2024, 12, 3), datetime(2024, 1, 8)),  # 330 days, 20026537
            (datetime(2027, 12, 1), datetime(2025, 6, 12)),  # 902 days, 20030639
        ],
    )
    def test_every_shape_the_corpus_prints_is_handled_the_same_way(self, traded, told):
        clause = _when_they_were_told(_Txn(traded, told), _Filing(datetime(2025, 9, 1)))
        assert "before the transaction it reports" in clause
        assert " days after the trade" not in clause
        assert "days after that" not in clause

    def test_the_finding_still_fires_on_such_a_row(self, db_session, seeded):
        """An unusable notification date is not a reason to say nothing."""
        _row(db_session, seeded[1], datetime(1935, 3, 28))
        found = [
            a
            for a in TradeAnalyzer().analyze_member(db_session, seeded[0].id)
            if a["anomaly_type"] == "late_filing"
        ]
        assert len(found) == 1
        assert int(found[0]["computed_value"]) == 146
        assert "before the transaction it reports" in found[0]["description"]

    def test_a_same_day_notification_is_not_treated_as_impossible(self):
        clause = _when_they_were_told(
            _Txn(datetime(2025, 3, 13), datetime(2025, 3, 13)), _Filing(datetime(2025, 5, 1))
        )
        assert "before the transaction" not in clause
        assert "notified of it on 2025-03-13" in clause


@pytest.fixture
def seeded(db_session):
    m = Member(
        bioguide_id="NT00001",
        first_name="Note",
        last_name="Ified",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="PA",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    d = Disclosure(
        member_id=m.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 9, 1),
        document_id="NT-1",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return m, d


def _row(db, disclosure, told):
    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 4, 8),
            transaction_type=TransactionType.PURCHASE,
            description="Cleveland-Cliffs Inc. Common Stock",
            ticker="CLF",
            amount_min=Decimal("100001"),
            amount_max=Decimal("250000"),
            owner="Self",
            notification_date=told,
        )
    )
    db.commit()


class TestTheColumnSurvivesTheRoundTrip:
    def test_it_is_stored_and_read_back(self, db_session, seeded):
        _row(db_session, seeded[1], datetime(2024, 8, 20))
        stored = db_session.query(Transaction).one()
        assert stored.notification_date == datetime(2024, 8, 20)

    def test_the_finding_still_fires_and_now_explains_itself(self, db_session, seeded):
        _row(db_session, seeded[1], datetime(2024, 8, 20))
        found = [
            a
            for a in TradeAnalyzer().analyze_member(db_session, seeded[0].id)
            if a["anomaly_type"] == "late_filing"
        ]
        assert len(found) == 1, "a late notification must never suppress the finding"
        assert "134 days after the trade" in found[0]["description"]
        assert int(found[0]["computed_value"]) == 146

    def test_a_row_notified_promptly_is_scored_identically(self, db_session, seeded):
        """The one thing the rejected guard would have changed."""
        _row(db_session, seeded[1], datetime(2024, 8, 25))
        found = [
            a
            for a in TradeAnalyzer().analyze_member(db_session, seeded[0].id)
            if a["anomaly_type"] == "late_filing"
        ]
        # Filed 7 days after notification -- prompt by the 30-day prong, and
        # still 146 days after the trade, which is what the statute caps.
        assert len(found) == 1
        assert int(found[0]["computed_value"]) == 146

    def test_a_row_with_no_notification_reads_exactly_as_before(self, db_session, seeded):
        _row(db_session, seeded[1], None)
        found = [
            a
            for a in TradeAnalyzer().analyze_member(db_session, seeded[0].id)
            if a["anomaly_type"] == "late_filing"
        ]
        assert len(found) == 1
        assert "notified" not in found[0]["description"]


def test_the_orchestrator_stores_what_the_parser_read():
    """Structural, because the defect was two call sites silently omitting it."""
    source = open("src/ingestion/orchestrator.py").read()
    assert source.count('notification_date=txn_data.get("notification_date")') == 2, (
        "a _store_*_data call site no longer passes the notification date, so "
        "the parser reads it and the database forgets it -- the original defect"
    )


def test_the_compliance_docstring_no_longer_says_it_is_undisclosed():
    import src.analysis.compliance as compliance

    assert "awareness dates are not disclosed" not in (compliance.__doc__ or "")
    assert "House PTRs DO print a Notification Date" in (compliance.__doc__ or "")


class TestTheSenatesFilerComment:
    """eFD prints a Comment column, the parser mapped it, and nothing kept it.

    `_COLUMN_ALIASES` has always had `("comment", "comment")`;
    `_rows_to_transactions` read the cell and left it out of the dict. Two rows
    in the local corpus carry the note that matters:

        While no immediate PTR required, provided to clearly denote basis for
        the renamed asset on the 2023 annual report that was previously named
        CEQP.

    Both are Sen. Hagerty's -- the Crestwood/Energy Transfer and Equitrans/EQT
    corporate actions -- and this project publishes **both** as late STOCK Act
    filings. He wrote on the form that he believed no report was due and filed
    anyway, for clarity.

    **The comment is published and never acted on.** An audit finding asked for
    a predicate suppressing a late-filing finding on rows like these. Two rows
    is not evidence enough to build a rule that withdraws accusations, and the
    rule's shape is one this project refuses on principle: an accusation is not
    cancelled by the accused's own unverifiable note. Publishing what they
    wrote lets a reader weigh it, which is the same posture #101 took on
    "Amended" -- except that one is the FORM's answer in a fixed vocabulary,
    and this one is free text.

    Measured: 1,517 comment cells across 244 local Senate filings, 188
    substantive, 1,338 the placeholder "n/a" -- which is why the placeholder is
    stored as NULL rather than put on a card.
    """

    def test_the_placeholder_is_not_stored_as_a_comment(self):
        from src.parsing.senate_html_parser import _comment

        for placeholder in ("n/a", "N/A", " -- ", "-", "", "  ", None, "none"):
            assert _comment(placeholder) is None

    def test_a_real_comment_survives_verbatim(self):
        from src.parsing.senate_html_parser import _comment

        text = (
            "While no immediate PTR required, provided to clearly denote basis "
            "for the renamed asset on the 2023 annual report that was "
            "previously named CEQP."
        )
        assert _comment(f"  {text}  ") == text

    def test_the_parser_puts_it_in_the_dict(self):
        """The defect exactly: mapped in the aliases, absent from the row."""
        source = open("src/parsing/senate_html_parser.py").read()
        assert '("comment", "comment")' in source, "the column alias is gone"
        assert '"filer_comment": _comment(column("comment"))' in source, (
            "the Senate parser reads the comment column and drops the value "
            "again -- the original defect"
        )

    def test_the_orchestrator_stores_it(self):
        source = open("src/ingestion/orchestrator.py").read()
        assert source.count('filer_comment=txn_data.get("filer_comment")') == 2

    def test_nothing_scores_against_it(self):
        """Structural, because the rejected fix was a suppression predicate."""
        for module in ("src/analysis/trade_analyzer.py", "src/analysis/compliance.py"):
            source = open(module).read()
            assert "filer_comment" not in source, (
                f"{module} reads the filer's own note; it is published from the "
                "stored row, never used to decide whether to publish"
            )
