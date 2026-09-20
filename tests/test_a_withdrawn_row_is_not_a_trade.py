"""The form prints three filing statuses and this project had read two.

Under every row, a House PTR prints:

    F S: New        the disclosure as first reported
    F S: Amended    a correction, which #101 taught the late-filing clock
    F S: Deleted    the filer struck the entry out

Nothing had ever read the third. A scan of all 2,399 local filings finds

    new       9,531
    amended      11
    deleted       3

and the three are Del. Eleanor Holmes Norton's withdrawn Berkshire Hathaway
sale (20025053) and two Minnesota municipal purchases (20030475), each printed
under `F S: Deleted`.

What the one that reached a reader did:

    Run(length=5, span_days=39, days=3, first=2024-04-04)   with the withdrawn row
    None                                                     without it

-- a published "same-direction trades on 3 days (5)" about a named member, one
fifth of which the filer had told the Clerk to disregard.

The row is still stored. It is printed on the form, and
`test_every_printed_trade_is_stored.py` balances printed lines against stored
rows, so dropping it at the parser would break the reconciliation that proves
nothing is lost. It is excluded where trades are counted instead, at the one
funnel every detector already shares.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import date, datetime

import pytest

from src.analysis import restatements
from src.analysis.restatements import (
    WITHDRAWN,
    drop_restatements,
    withdrawn,
)
from src.db.models import TransactionType
from src.parsing.ptr_parser import AMENDED, DELETED, NEW, _filing_status_in


class _Row:
    def __init__(self, id, disclosure_id, description, filing_status=NEW, when=None):
        self.id = id
        self.disclosure_id = disclosure_id
        self.description = description
        self.filing_status = filing_status
        self.transaction_date = when or date(2024, 4, 8)
        self.transaction_type = TransactionType.SALE
        self.ticker = None
        self.amount_min = None
        self.amount_max = None
        self.owner = ""


class TestTheFormsOwnWord:
    """Read verbatim from the footnote the Clerk prints under the row."""

    def test_deleted_is_read(self):
        assert _filing_status_in("F S: Deleted") == DELETED

    def test_the_other_two_still_are(self):
        assert _filing_status_in("F S: New") == NEW
        assert _filing_status_in("F S: Amended") == AMENDED

    def test_the_small_caps_glyphs_that_read_as_nul(self):
        # The form sets "FILING STATUS" in small caps; pdfplumber returns the
        # glyphs as NUL bytes, which is why the pattern matches "F S:".
        assert _filing_status_in("F\x00 S\x00 : Deleted\x00") == DELETED

    def test_a_word_the_form_does_not_print_is_not_invented(self):
        assert _filing_status_in("F S: Pending") is None
        assert _filing_status_in("nothing here") is None


class TestItIsNotCounted:
    def test_a_struck_out_row_does_not_reach_a_detector(self):
        kept = drop_restatements(
            [
                _Row(1, 10, "Berkshire Hathaway Inc. New S (partial)"),
                _Row(2, 11, "2000119685 Berkshire Hathaway Inc. New", DELETED),
            ],
            {10: datetime(2024, 5, 9), 11: datetime(2024, 5, 20)},
        )
        assert [row.id for row in kept] == [1]

    def test_the_row_the_deletion_withdraws_survives(self):
        # Deliberate, and the conservative direction: the two copies cannot be
        # paired by content (the withdrawal carries the House record id at the
        # head of its description and the original does not), so taking down
        # the withdrawal alone is what the document supports.
        kept = drop_restatements(
            [_Row(1, 10, "Berkshire Hathaway Inc. New S (partial)")],
            {10: datetime(2024, 5, 9)},
        )
        assert [row.id for row in kept] == [1]

    def test_a_filing_of_nothing_but_withdrawals_is_empty_not_an_error(self):
        assert (
            drop_restatements(
                [_Row(1, 10, "x", DELETED), _Row(2, 10, "y", DELETED)],
                {10: datetime(2025, 5, 6)},
            )
            == []
        )

    def test_a_row_with_no_filing_status_is_kept(self):
        # NULL means "not read yet" -- every row stored before #101, and every
        # Senate row, where the column does not exist. It must never mean
        # "withdrawn".
        row = _Row(1, 10, "x", filing_status=None)
        assert not withdrawn(row)
        assert [r.id for r in drop_restatements([row], {10: datetime(2024, 5, 9)})] == [1]

    def test_a_projection_that_lacks_the_column_is_kept_not_dropped(self):
        class Narrow:
            id = 1
            disclosure_id = 10

        assert not withdrawn(Narrow())


class TestEveryProjectionCarriesTheColumn:
    """Structural, because the default in `withdrawn` is silence.

    `drop_restated_records` is fed hand-built row tuples by `significance` and
    `tier2_detectors`. A projection that forgets `filing_status` keeps the
    struck-out rows and says nothing, which is the failure mode this project
    keeps finding. So the call sites are read rather than trusted.
    """

    @pytest.mark.parametrize(
        "module",
        ["src/analysis/significance.py", "src/analysis/tier2_detectors.py"],
    )
    def test_every_query_feeding_the_funnel_selects_filing_status(self, module):
        tree = ast.parse(textwrap.dedent(open(module).read()))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "drop_restated_records"
        ]
        assert calls, f"{module} no longer feeds drop_restated_records"
        for call in calls:
            dumped = ast.dump(call) if call.args else ""
            # The tier2 site passes a name bound to the query above it, so fall
            # back to the whole module when the call does not carry the columns.
            haystack = dumped if "owner" in dumped else ast.dump(tree)
            assert "filing_status" in haystack, (
                f"{module}: a projection feeding drop_restated_records omits "
                "filing_status, so withdrawn rows would be counted"
            )

    def test_the_funnel_still_applies_the_rule(self):
        source = textwrap.dedent(inspect.getsource(restatements.drop_restatements))
        tree = ast.parse(source)
        assert any(
            isinstance(node, ast.Name) and node.id == "withdrawn" for node in ast.walk(tree)
        ), "drop_restatements no longer excludes withdrawn rows"


def test_the_constant_matches_what_the_parser_writes():
    # Two modules, one string. If either moves, the filter stops matching and
    # nothing fails loudly, so this is the only assertion holding them together.
    assert WITHDRAWN == DELETED == "Deleted"
