"""A spouse's trade is not the member's trade.

Found by pointing a hostile reader at the project's own published findings. It
picked one -- "Member made 6 consecutive trades in the same direction over 29
days", about a named sitting congressman -- and took it apart:

    five of the six rows were not his. Two carried owner='Spouse'/'Joint'
    outright; three more still had the House owner codes "SP "/"JT " sitting at
    the front of the asset description while the owner column read 'Self'.
    Strip them and his entire record is one Kinder Morgan sale.

The House PTR form prints the owner in its own column -- SP spouse, JT joint,
DC dependent child, blank for the filer. When pdfplumber fails to split that
column off, the code survives at the head of the description and the owner
column reads empty. `_normalize_owner("")` returned "Self", turning "we could
not read this" into the affirmative claim that the member owns it. The text
fallback path did not even ask: it hardcoded "Self".

Measured over a 10,594-transaction corpus built from real House PTRs:

    rows whose description begins SP / JT / DC : 2,488  (23.5%)
    of those, stored as owner='Self'          : 2,488  (100%)

1,318 spouse trades, 988 joint, 182 dependent child -- every one published as
something the member did personally. The owner column works elsewhere (1,776
Spouse, 1,539 Joint, 241 Dependent Child), so this is not "the parser cannot
read owners". It is the parser asserting Self whenever it failed to.

Attribution is the one thing a disclosure site cannot get wrong quietly. Every
finding that counts a member's trades -- high_trading_frequency, trade_clustering,
volume_spikes, sector_concentration, large_trade -- counted the spouse's too.
"""

from __future__ import annotations

import pytest

from src.parsing.ptr_parser import PTRParser, _owner_from_description


class TestTheCodeTheDocumentPrinted:
    @pytest.mark.parametrize(
        "description,owner,rest",
        [
            ("SP Chevron Corporation Common Stock", "Spouse", "Chevron Corporation Common Stock"),
            ("JT AT&T Inc. (T) [ST]", "Joint", "AT&T Inc. (T) [ST]"),
            ("DC Vanguard 500 Index Fund", "Dependent Child", "Vanguard 500 Index Fund"),
            ("sp lowercase is still a spouse", "Spouse", "lowercase is still a spouse"),
        ],
    )
    def test_a_leading_code_names_the_owner_and_leaves_the_asset(self, description, owner, rest):
        assert _owner_from_description(description) == (owner, rest)

    @pytest.mark.parametrize(
        "description",
        [
            "SPY ETF Trust",
            "JTEKT Corporation",
            "DCP Midstream LP",
            "Kinder Morgan, Inc. (KMI) [ST]",
            "Spartan Nash Co",
        ],
    )
    def test_a_ticker_that_merely_starts_with_those_letters_is_untouched(self, description):
        # The whole fix is worthless if it eats SPY or JTEKT. The word boundary
        # is what separates a printed owner code from the first three letters of
        # a company name.
        assert _owner_from_description(description) == (None, description)

    def test_an_ordinary_description_is_returned_unchanged(self):
        assert _owner_from_description("Apple Inc. (AAPL) [ST]") == (None, "Apple Inc. (AAPL) [ST]")

    def test_empty_input_does_not_explode(self):
        assert _owner_from_description("") == (None, "")


class TestTheFallbackPathNoLongerAssertsSelf:
    """The path a row reaches when the table reader could not split the cell.

    Which is precisely when the owner code is still in the description -- so
    hardcoding "Self" here was wrong in exactly the cases it was reached.
    """

    def test_a_spouse_row_read_by_the_text_path_is_the_spouse(self):
        parser = PTRParser()
        row = parser._parse_collapsed_cell(
            "SP Chevron Corporation Common Stock S 04/10/2024 $1,001 - $15,000"
        )
        assert row is not None
        assert row["owner"] == "Spouse"
        assert row["description"].startswith("Chevron")

    def test_a_filers_own_row_is_still_self(self):
        parser = PTRParser()
        row = parser._parse_collapsed_cell(
            "Kinder Morgan, Inc. Common Stock S 04/10/2024 $1,001 - $15,000"
        )
        assert row is not None
        assert row["owner"] == "Self"

    def test_the_code_is_not_left_in_the_stored_description(self):
        # Otherwise the ticker extractor and every sector rule see "SP" as part
        # of the asset name.
        parser = PTRParser()
        row = parser._parse_collapsed_cell("JT AT&T Inc. (T) S 04/10/2024 $1,001 - $15,000")
        assert row is not None
        assert not row["description"].upper().startswith("JT ")


class TestTheTableFallsBackToTheCodeToo:
    """The table path had the same hole, from the other direction.

    Here the owner COLUMN exists but arrives empty -- pdfplumber split the row,
    just not that cell -- and the code is in the asset text. `_normalize_owner("")`
    answered "Self", so the document's own marking was overruled by a default.
    """

    COLUMNS = {"asset": 0, "type": 1, "date": 2, "amount": 3, "owner": 4}

    def test_an_empty_owner_column_defers_to_the_code_in_the_description(self):
        row = PTRParser()._parse_table_row(
            ["SP Chevron Corporation Common Stock", "S", "04/10/2024", "$1,001 - $15,000", ""],
            self.COLUMNS,
        )
        assert row is not None
        assert row["owner"] == "Spouse", (
            "the document printed SP and the owner column was unreadable; "
            "defaulting to Self publishes the spouse's trade as the member's"
        )
        assert row["description"].startswith("Chevron")

    def test_a_populated_owner_column_still_wins_for_ordinary_rows(self):
        row = PTRParser()._parse_table_row(
            ["Apple Inc. (AAPL)", "P", "04/10/2024", "$1,001 - $15,000", "Spouse"],
            self.COLUMNS,
        )
        assert row is not None
        assert row["owner"] == "Spouse"

    def test_a_filers_own_row_with_a_blank_owner_is_still_self(self):
        # The default is only wrong when the document said otherwise.
        row = PTRParser()._parse_table_row(
            ["Kinder Morgan, Inc. (KMI)", "S", "04/10/2024", "$1,001 - $15,000", ""],
            self.COLUMNS,
        )
        assert row is not None
        assert row["owner"] == "Self"
