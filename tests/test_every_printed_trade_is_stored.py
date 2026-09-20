"""The parser under-read by 5.9%, silently.

An adversarial audit of this project claimed a large class of DUPLICATE rows.
Six filings counted line by line against the Clerk's PDFs showed the parser was
right in all six, and D18 records that the class does not exist. The same
counting, run the other way, found what does:

    printed transaction lines   2,062
    stored rows                 1,941
    missing                       121   (5.9%), in 13 of 79 filings

Three separate mechanisms, all visible in one 41-page filing (20030387), which
is why it is vendored here:

**The header was assumed to be row 0.** pdfplumber emits a blank leading row
whenever a page's ruling lines start fractionally above the header text. The
blank row failed the "does this look like a transaction table" test and the
WHOLE TABLE was skipped -- two pages, sixteen disclosed trades, no error and no
warning.

**`extract_tables` drops the last record on a page.** Those rows are printed in
the text layer and absent from every table, so no amount of table-reading finds
them. They are recovered by reconciling against the text.

**A bond prints its maturity inside its own name.**

    Wells Fargo 6.491 10/23/34 '33 MTN S 05/13/2025 05/15/2025 $50,001 -
                      ^^^^^^^^ maturity   ^^^^^^^^^^ the trade

The first date on the line was taken as the transaction date, so that row was
stored as 2034-10-23 -- nine years in the future. It is how a "same-direction
trades on 3 days" finding got a third day forty days from the other two, and
how a late-filing finding came to claim 742 days.

Under-counting is the safe direction for an accusation and it is still wrong:
it breaks the one property this project has been building, that a reader can
check a published number against the document and have it reconcile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.parsing.ptr_parser import ParseQuality, PTRParser, _header_row_index

FIXTURE = Path(__file__).parent / "fixtures" / "ptr_reconciliation" / "20030387.json"

# The shape of a printed transaction line: asset, type token, trade date.
PRINTED = re.compile(r"\b[PSE]( \(partial\))? \d{2}/\d{2}/\d{4}")


@pytest.fixture
def filing():
    if not FIXTURE.exists():
        pytest.skip("reconciliation fixture not vendored")
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def parser():
    return PTRParser()


class TestTheHeaderIsFoundNotAssumed:
    def test_a_blank_first_row_does_not_discard_the_table(self):
        table = [
            ["", "", "", "", ""],
            ["ID", "Owner", "Asset", "Transaction\nType", "Date"],
            ["", "", "Apple Inc. (AAPL) [ST]", "P", "05/08/2025"],
        ]
        assert _header_row_index(table) == 1

    def test_a_header_in_the_usual_place_is_still_found(self):
        assert (
            _header_row_index([["Asset", "Transaction Type", "Date"], ["x", "P", "01/02/2025"]])
            == 0
        )

    def test_a_table_with_no_header_is_skipped(self):
        assert _header_row_index([["a", "b"], ["c", "d"]]) is None

    def test_one_header_word_is_not_enough(self):
        """ "Asset Management" and "Global Transaction Services" are real fund
        names. Mistaking a data row for the header would drop it and everything
        above it."""
        assert _header_row_index([["Fidelity Asset Manager 50%", "", ""]]) is None
        assert _header_row_index([["Global Transaction Services Corp", "", ""]]) is None

    def test_it_does_not_search_arbitrarily_far(self):
        """A header found ten rows down means the rows above it were data, and
        skipping them is the bug this exists to prevent."""
        deep = [["x", "y"]] * 8 + [["Asset", "Transaction Type"]]
        assert _header_row_index(deep) is None


class TestTheTableIsStillWhereTheRowsComeFrom:
    """The reconciliation is a safety net, not the mechanism — and it MASKS the
    header bug unless something checks for that directly.

    A row recovered from the printed page is a weaker read: the description is
    truncated at the column edge, the owner column is gone, and it is flagged
    so the confidence score can say so. Sixteen rows arriving that way instead
    of through their columns would be stored as owner "Self" whatever the
    filing said — the attribution defect #99 exists to prevent.

    So restoring `table[0]` as the header must fail a test, and with only the
    end-to-end count it did not: the recovery quietly made up the difference.
    """

    def test_a_blank_header_page_yields_its_rows_through_the_table_path(self, parser, filing):
        blank_first = [
            t
            for t in filing["tables"]
            if t and not any(str(c).strip() for c in t[0] if c) and _header_row_index(t) is not None
        ]
        assert blank_first, "fixture no longer contains a blank-leading-row table"

        rows = parser._parse_tables(blank_first, ParseQuality())

        assert len(rows) >= 8 * len(blank_first), (
            "a table whose first row is blank produced nothing through the column "
            "path; the text recovery would hide that by re-reading the same trades "
            "without their owner column"
        )

    def test_the_recovery_is_a_minority_of_the_rows(self, parser, filing):
        """If this ratio jumps, the table path has broken and the net count
        stays right only because the fallback is carrying it."""
        quality = ParseQuality()
        from_tables = parser._parse_tables(filing["tables"], quality)
        recovered = parser._recover_printed_rows(filing["text"], from_tables, quality)

        total = len(from_tables) + len(recovered)
        assert len(recovered) / total < 0.10, (
            f"{len(recovered)} of {total} rows came from the text layer rather than their columns"
        )


class TestTheTradeDateIsNotTheMaturityDate:
    LINE = "Wells Fargo 6.491 10/23/34 '33 MTN S 05/13/2025 05/15/2025 $50,001 - $100,000"

    def test_the_bond_that_was_stored_in_2034(self, parser):
        txn = parser._parse_text_line(parser._spell_out_type_letter(self.LINE))

        assert txn["transaction_date"].year == 2025
        assert txn["transaction_date"].strftime("%m/%d/%Y") == "05/13/2025"

    def test_the_maturity_stays_in_the_asset_name(self, parser):
        """It is part of what the filing calls the instrument, so dropping it
        would make two different bonds from the same issuer indistinguishable."""
        txn = parser._parse_text_line(parser._spell_out_type_letter(self.LINE))

        assert "10/23/34" in txn["description"]

    def test_an_ordinary_line_is_unaffected(self, parser):
        txn = parser._parse_text_line(
            parser._spell_out_type_letter(
                "Apple Inc. - Common Stock (AAPL) [ST] P 05/08/2025 05/15/2025 $1,001 - $15,000"
            )
        )

        assert txn["transaction_date"].strftime("%m/%d/%Y") == "05/08/2025"
        assert txn["transaction_type"] == "purchase"


class TestTheKeyThatDecidesWhatIsAlreadyStored:
    """Both sides of the reconciliation run through `_row_key`. Anything it
    normalises on one side and not the other stores a trade twice, which is
    worse than the under-reading this change exists to fix."""

    def test_the_house_id_column_is_ignored(self, parser):
        a = parser._row_key("2000114315 SP Alibaba Group Holding Limited", None, "sale")
        b = parser._row_key("SP Alibaba Group Holding Limited", None, "sale")
        assert a == b

    def test_a_cusip_is_not_mistaken_for_an_id(self, parser):
        """ "097023DC6" is a CUSIP. Stripping leading digits without requiring
        the separator ate six of them and stored the trade twice."""
        assert parser._row_key("097023DC6 [CS]", None, "purchase")[0].startswith("097023dc6")

    def test_a_trailing_type_marker_is_ignored(self, parser):
        a = parser._row_key("SAP SE ADS (SAP) [ST] S (partial)", None, "sale")
        b = parser._row_key("SAP SE ADS (SAP) [ST]", None, "sale")
        assert a == b

    def test_the_owner_code_is_ignored(self, parser):
        assert parser._row_key("SP Microsoft Corp", None, "sale") == parser._row_key(
            "Microsoft Corp", None, "sale"
        )

    def test_two_different_assets_do_not_collide(self, parser):
        assert parser._row_key("Apple Inc. Common Stock", None, "sale") != parser._row_key(
            "Alphabet Inc. Class C", None, "sale"
        )

    def test_amount_is_deliberately_not_in_the_key(self, parser):
        """A member really does sell the same stock twice on one day in two
        bands. Those are two rows and both must survive -- collapsing them
        would re-commit the duplication error D18 refuted."""
        import inspect

        assert "amount" not in inspect.getsource(PTRParser._row_key).split('"""')[2]


class TestTheWholeFiling:
    """20030387, end to end: every printed line reaches the database."""

    def test_every_printed_transaction_is_stored(self, parser, filing):
        printed = sum(1 for line in filing["text"].split("\n") if PRINTED.search(line))
        assert printed == filing["printed_transaction_lines"]

        quality = ParseQuality()
        rows = parser._parse_tables(filing["tables"], quality)
        rows += parser._recover_printed_rows(filing["text"], rows, quality)

        assert len(rows) == printed, (
            f"{printed} transaction lines printed, {len(rows)} stored — "
            "a disclosed trade that never reaches the database is invisible"
        )

    def test_nothing_is_stored_twice(self, parser, filing):
        import collections

        quality = ParseQuality()
        rows = parser._parse_tables(filing["tables"], quality)
        rows += parser._recover_printed_rows(filing["text"], rows, quality)

        keys = collections.Counter(
            parser._row_key(
                r.get("description"), r.get("transaction_date"), r.get("transaction_type")
            )
            for r in rows
        )
        printed = collections.Counter()
        for raw in filing["text"].split("\n"):
            m = parser._PRINTED_ROW.match(raw.strip())
            if m:
                printed[
                    parser._row_key(
                        m.group("asset"),
                        parser._parse_date(m.group("date")),
                        parser._TYPE_WORDS.get(m.group("type")),
                    )
                ] += 1

        assert not (keys - printed), f"stored but not printed: {dict(keys - printed)}"

    def test_no_trade_is_dated_in_the_future(self, parser, filing):
        """The maturity-date bug put a 2025 sale in 2034."""
        quality = ParseQuality()
        rows = parser._parse_tables(filing["tables"], quality)
        rows += parser._recover_printed_rows(filing["text"], rows, quality)

        late = [r for r in rows if r.get("transaction_date") and r["transaction_date"].year > 2026]
        assert not late, [str(r["transaction_date"]) for r in late[:3]]

    def test_the_recovered_rows_are_reported_as_recovered(self, parser, filing):
        """Confidence scoring reads this; a row recovered off the printed page
        must not be presented as a clean column read."""
        quality = ParseQuality()
        rows = parser._parse_tables(filing["tables"], quality)
        recovered = parser._recover_printed_rows(filing["text"], rows, quality)

        assert recovered
        assert all(r.get("recovered_from_collapsed_row") for r in recovered)
        assert quality.rows_recovered >= len(recovered)
