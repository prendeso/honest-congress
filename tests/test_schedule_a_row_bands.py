"""Reading Schedule A from the grid the form draws, rather than from a guess.

`extract_tables` finds four tables on Earl Carter's first page and twenty-two
holdings in his whole filing. The document draws forty-four row bands and names
forty-four holdings, among them "Guardian Point Capital [HE] $5,000,001 -
$25,000,000", which is in the text layer and in no table pdfplumber can see.

Measured over 16 House annual filings whose documents name 1,376 holdings::

    table reader   722 holdings    $312,182,404 of disclosed value
    banded reader  1,420 holdings  $847,571,007

Value grows faster than count -- 2.71x against 1.97x -- because the rows that
were lost are the tall ones, a row is tall when its description or its value
band wraps, and "$5,000,001 - $25,000,000" wraps where "$1,001 - $15,000" does
not. The holdings that went missing were systematically the large ones.

The fixtures here are hand-built pages rather than PDFs, because what is being
tested is the reading of a geometry: which rectangles bound a row, which words
fall in which column, and where Schedule A stops. A real PDF would test
pdfplumber as much as this code, and would not let a single edge be moved to see
what breaks.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.parsing.pdf_parser import (
    DisclosureParser,
    _cells_in_band,
    _header_columns,
    _schedule_a_bands,
    _visual_lines,
)

# The column edges the House annual form uses, taken from Carter's filing.
ASSET_X, OWNER_X, VALUE_X, TYPE_X, INCOME_X, TX_X = 25.3, 262.3, 301.3, 383.8, 466.3, 533.8
PAGE_WIDTH, PAGE_HEIGHT = 612.0, 792.0


def word(text: str, x0: float, top: float) -> Dict[str, Any]:
    return {"text": text, "x0": x0, "x1": x0 + 6.0 * len(text), "top": top, "bottom": top + 8.0}


def header_words(top: float) -> List[Dict[str, Any]]:
    """The Schedule A column header, as the form prints it."""
    return [
        word("Asset", ASSET_X, top),
        word("Owner", OWNER_X, top),
        word("Value", VALUE_X, top),
        word("of", VALUE_X + 34, top),
        word("Asset", VALUE_X + 46, top),
        word("Income", TYPE_X, top),
        word("Type(s)", TYPE_X + 42, top),
        word("Income", INCOME_X, top),
        word("Tx.", TX_X, top),
        word(">", TX_X + 20, top),
    ]


def band_rects(*bounds: tuple[float, float]) -> List[Dict[str, Any]]:
    """The left border segment the form draws beside every row."""
    return [{"x0": 21.7, "x1": 22.5, "top": top, "bottom": bottom} for top, bottom in bounds]


class FakePage:
    def __init__(self, words: List[Dict[str, Any]], rects: List[Dict[str, Any]]):
        self._words = words
        self.rects = rects
        self.width = PAGE_WIDTH
        self.height = PAGE_HEIGHT

    def extract_words(self) -> List[Dict[str, Any]]:
        return list(self._words)


class FakePdf:
    def __init__(self, *pages: FakePage):
        self.pages = list(pages)


def carter_page() -> FakePage:
    """Three of Carter's holdings, including the one with a wrapped value band."""
    words = header_words(100.0)
    words += [
        # A one-line row.
        word("Ameris", ASSET_X, 120.0),
        word("Bank", ASSET_X + 40, 120.0),
        word("[BA]", ASSET_X + 74, 120.0),
        word("$1,000,001", VALUE_X, 120.0),
        word("-", VALUE_X + 62, 120.0),
        word("$5,000,000", VALUE_X, 130.0),
        word("Interest", TYPE_X, 120.0),
        word("$5,001", INCOME_X, 120.0),
        word("-", INCOME_X + 40, 120.0),
        word("$15,000", INCOME_X, 130.0),
        # The holding the table reader loses.
        word("Guardian", ASSET_X, 150.0),
        word("Point", ASSET_X + 52, 150.0),
        word("Capital", ASSET_X + 86, 150.0),
        word("[HE]", ASSET_X + 130, 150.0),
        word("$5,000,001", VALUE_X, 150.0),
        word("-", VALUE_X + 62, 150.0),
        word("$25,000,000", VALUE_X, 160.0),
        word("Capital", TYPE_X, 150.0),
        word("Gains", TYPE_X + 44, 150.0),
        word("$1,000,001", INCOME_X, 150.0),
        word("-", INCOME_X + 62, 150.0),
        word("$5,000,000", INCOME_X, 160.0),
        # The form's own comment row: a band, but not a holding.
        word("D:", ASSET_X, 180.0),
        word("EIF", ASSET_X + 14, 180.0),
    ]
    rects = band_rects((115.0, 145.0), (145.0, 175.0), (175.0, 190.0))
    return FakePage(words, rects)


class TestABandIsWhateverTheFormDrew:
    def test_every_drawn_band_below_the_header_is_a_row(self):
        bands = _schedule_a_bands(carter_page())
        assert [(top, bottom) for top, bottom, _ in bands] == [
            (115.0, 145.0),
            (145.0, 175.0),
            (175.0, 190.0),
        ]

    def test_the_columns_come_from_the_header_the_page_prints(self):
        _, _, columns = _schedule_a_bands(carter_page())[0]
        assert columns == [
            ASSET_X - 2,
            OWNER_X - 2,
            VALUE_X - 2,
            TYPE_X - 2,
            INCOME_X - 2,
            TX_X - 2,
            PAGE_WIDTH,
        ]

    def test_a_page_with_no_schedule_a_header_yields_nothing(self):
        page = FakePage([word("Asset", ASSET_X, 100.0)], band_rects((115.0, 145.0)))
        assert _schedule_a_bands(page) == []

    def test_a_border_hairline_is_not_a_row(self):
        # The form draws 0.8pt segments at the corners of its grid. Reading one
        # as a row would put an empty holding into the database.
        page = carter_page()
        page.rects += band_rects((190.0, 190.8))
        assert all(bottom - top > 3.0 for top, bottom, _ in _schedule_a_bands(page))

    def test_schedule_a_stops_where_the_next_schedule_starts(self):
        # Schedule B carries the same Asset and Owner columns and its Amount
        # column sits where Value of Asset does, so a reader that does not stop
        # here stores every trade in the filing as a holding.
        page = carter_page()
        page._words += [
            word("Asset", ASSET_X, 200.0),
            word("Owner", OWNER_X, 200.0),
            word("Date", VALUE_X, 200.0),
            word("Tx.", TYPE_X, 200.0),
            word("Amount", INCOME_X, 200.0),
            word("Cap.", TX_X, 200.0),
        ]
        page.rects += band_rects((205.0, 235.0))
        assert [(top, bottom) for top, bottom, _ in _schedule_a_bands(page)] == [
            (115.0, 145.0),
            (145.0, 175.0),
            (175.0, 190.0),
        ]


class TestWordsLandInTheColumnTheyArePrintedIn:
    def test_a_band_is_split_at_the_column_edges(self):
        page = carter_page()
        _, _, columns = _schedule_a_bands(page)[0]
        cells = _cells_in_band(page.extract_words(), 115.0, 145.0, columns)
        assert cells[0] == "Ameris Bank [BA]"
        assert cells[2] == "$1,000,001 - $5,000,000"
        assert cells[3] == "Interest"
        assert cells[4] == "$5,001 - $15,000"

    def test_a_wrapped_value_band_is_rejoined_in_reading_order(self):
        page = carter_page()
        _, _, columns = _schedule_a_bands(page)[1]
        cells = _cells_in_band(page.extract_words(), 145.0, 175.0, columns)
        assert cells[2] == "$5,000,001 - $25,000,000"

    def test_lines_are_grouped_by_where_they_are_printed(self):
        lines = _visual_lines([word("b", 50.0, 10.0), word("a", 20.0, 11.0), word("c", 5.0, 40.0)])
        assert [[w["text"] for w in line] for line in lines] == [["a", "b"], ["c"]]


class TestTheHeaderIsReadNotAssumed:
    def test_the_schedule_a_header_gives_one_edge_per_column(self):
        xs = _header_columns(header_words(100.0), ("Asset", "Owner", "Value", "Income", "Income"))
        assert xs == [ASSET_X, OWNER_X, VALUE_X, TYPE_X, INCOME_X]

    def test_a_line_missing_a_column_is_not_the_header(self):
        line = [word("Asset", ASSET_X, 100.0), word("Owner", OWNER_X, 100.0)]
        assert _header_columns(line, ("Asset", "Owner", "Value")) is None


class TestWhatTheReaderStores:
    def test_it_stores_the_holding_the_table_reader_loses(self):
        assets = DisclosureParser()._assets_from_row_bands(FakePdf(carter_page()))
        descriptions = [a["description"] for a in assets]
        assert "Guardian Point Capital [HE]" in descriptions

    def test_it_reads_the_value_band_the_document_discloses(self):
        assets = DisclosureParser()._assets_from_row_bands(FakePdf(carter_page()))
        guardian = next(a for a in assets if a["description"].startswith("Guardian"))
        # Carter's stored holdings used to top out at $1,000,000 -- this row, at
        # up to $25,000,000, was simply absent. That understatement is the whole
        # reason `wealth_vs_salary` is held.
        assert (guardian["value_min"], guardian["value_max"]) == (5000001, 25000000)

    def test_a_comment_row_is_not_a_holding(self):
        # "D: EIF" occupies a band of its own. Without the asset-class code test
        # it becomes a holding with no value and a description of somebody's
        # explanatory note.
        assets = DisclosureParser()._assets_from_row_bands(FakePdf(carter_page()))
        assert [a["description"] for a in assets] == [
            "Ameris Bank [BA]",
            "Guardian Point Capital [HE]",
        ]

    def test_a_document_that_draws_no_bands_falls_back_to_the_tables(self):
        # Nothing observed says every House form draws this grid, and an older
        # one that does not must still be read by the path that used to read it.
        assert DisclosureParser()._assets_from_row_bands(FakePdf(FakePage([], []))) == []
