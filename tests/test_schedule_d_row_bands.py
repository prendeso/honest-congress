"""Reading Schedule D the same way, and why debt is the half that flatters.

`_calculate_wealth_progression` SUBTRACTS liabilities, so a debt the parser
cannot read raises the member's apparent net worth. Schedule D was losing its
rows the same way Schedule A was, and this direction of the error is the one
nobody complains about.

Over 20 House annual filings, the table reader produced 114 liabilities of which
**97 (85%) carried no amount at all** -- not because the documents omit one, but
because the whole row had been flattened into the creditor field, band and all::

    creditor:   "Town & Country Bank November 2014 138 Acre Farm, House, Hay
                 & Cattle $100,001 -\\n$250,000"
    amount_min: None

Read from the bands the form draws, those same filings yield 78 debts, every one
with an amount, against 78 that the documents name. The count falls because the
old reader was also inventing debts: one filing that discloses two mortgages had
eight rows in the database.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.parsing.pdf_parser import DisclosureParser, _schedule_bands
from tests.test_schedule_a_row_bands import FakePage, FakePdf, band_rects, word

# Schedule D's columns: Owner | Creditor | Date Incurred | Type | Amount.
OWNER_X, CREDITOR_X, INCURRED_X, TYPE_X, AMOUNT_X = 25.3, 60.0, 200.0, 300.0, 470.0


def header_words(top: float) -> List[Dict[str, Any]]:
    return [
        word("Owner", OWNER_X, top),
        word("Creditor", CREDITOR_X, top),
        word("Date", INCURRED_X, top),
        word("Incurred", INCURRED_X + 30, top),
        word("Type", TYPE_X, top),
        word("Amount", AMOUNT_X, top),
        word("of", AMOUNT_X + 42, top),
        word("Liability", AMOUNT_X, top + 11),
    ]


def debt_page() -> FakePage:
    """One debt whose type and amount both wrap, and a schedule heading below."""
    words = header_words(100.0)
    words += [
        word("JT", OWNER_X, 130.0),
        word("Town", CREDITOR_X, 130.0),
        word("&", CREDITOR_X + 32, 130.0),
        word("Country", CREDITOR_X + 44, 130.0),
        word("Bank", CREDITOR_X + 90, 130.0),
        word("November", INCURRED_X, 130.0),
        word("2014", INCURRED_X + 56, 130.0),
        word("138", TYPE_X, 130.0),
        word("Acre", TYPE_X + 22, 130.0),
        word("Farm,", TYPE_X + 50, 130.0),
        word("House", TYPE_X, 140.0),
        word("$100,001", AMOUNT_X, 130.0),
        word("-", AMOUNT_X + 52, 130.0),
        word("$250,000", AMOUNT_X, 140.0),
        # Schedule E has no column header of this shape, so its heading is the
        # only thing that says Schedule D has ended.
        word("S", OWNER_X, 175.0),
        word("E:", OWNER_X + 12, 175.0),
        word("P", OWNER_X + 30, 175.0),
    ]
    # Contiguous, as the form draws them: each row's border segment abuts the
    # next, so a gap between bands never appears in a real filing.
    return FakePage(words, band_rects((125.0, 155.0), (155.0, 185.0)))


class TestADebtIsReadAsFiveCells:
    def test_the_row_is_split_rather_than_flattened(self):
        debts = DisclosureParser()._liabilities_from_row_bands(FakePdf(debt_page()))
        assert debts == [
            {
                "creditor": "Town & Country Bank",
                "description": "138 Acre Farm, House",
                "amount_min": 100001,
                "amount_max": 250000,
            }
        ]

    def test_the_owner_code_is_not_mistaken_for_the_creditor(self):
        # Every debt in the database used to be owed to a creditor called "JT"
        # or "SP", with the real lender demoted to the description.
        (debt,) = DisclosureParser()._liabilities_from_row_bands(FakePdf(debt_page()))
        assert debt["creditor"] == "Town & Country Bank"

    def test_a_band_with_no_amount_is_not_a_debt(self):
        # The form's trailing "None disclosed." note is a band of its own,
        # inside the schedule and above the next heading. Storing it puts a
        # creditor with no amount into a subtraction that decides somebody's
        # published net worth -- and that is how the old reader produced eight
        # liabilities for a filing that discloses two.
        page = debt_page()
        page._words = [w for w in page._words if w["top"] != 175.0]
        page._words += [
            word("None", OWNER_X, 160.0),
            word("disclosed.", OWNER_X + 30, 160.0),
            word("S", OWNER_X, 190.0),
            word("E:", OWNER_X + 12, 190.0),
            word("P", OWNER_X + 30, 190.0),
        ]
        page.rects += band_rects((185.0, 215.0))
        bands, _ = _schedule_bands(page, "D")
        assert len(bands) == 2
        assert len(DisclosureParser()._liabilities_from_row_bands(FakePdf(page))) == 1

    def test_schedule_d_stops_at_the_next_schedule_heading(self):
        bands, _ = _schedule_bands(debt_page(), "D")
        assert [(top, bottom) for top, bottom, _ in bands] == [(125.0, 155.0)]


class TestASchedulePageBreak:
    def test_a_schedule_that_spills_onto_the_next_page_keeps_its_columns(self):
        # Schedule D of Nancy Pelosi's 2024 annual runs one row onto the page
        # after it with NO header above that row, and that row is her largest
        # debt: a $25,000,001 - $50,000,000 brokerage margin account. A reader
        # that needs a header on every page drops exactly it.
        overflow = FakePage(
            [
                word("SP", OWNER_X, 20.0),
                word("City", CREDITOR_X, 20.0),
                word("National", CREDITOR_X + 26, 20.0),
                word("Securities", CREDITOR_X + 78, 20.0),
                word("Ongoing", INCURRED_X, 20.0),
                word("Brokerage", TYPE_X, 20.0),
                word("Margin", TYPE_X + 58, 20.0),
                word("$25,000,001", AMOUNT_X, 20.0),
                word("-", AMOUNT_X + 64, 20.0),
                word("$50,000,000", AMOUNT_X, 30.0),
            ],
            band_rects((15.0, 45.0)),
        )
        page_one = debt_page()
        page_one._words = [w for w in page_one._words if w["top"] != 175.0]
        page_one.rects = band_rects((125.0, 155.0))

        debts = DisclosureParser()._liabilities_from_row_bands(FakePdf(page_one, overflow))
        assert [d["creditor"] for d in debts] == [
            "Town & Country Bank",
            "City National Securities",
        ]
        assert debts[1]["amount_max"] == 50000000

    def test_a_schedule_closed_before_the_page_ends_does_not_carry(self):
        # `debt_page` ends with Schedule E's heading, so nothing on the next
        # page belongs to D -- and the next page here is another member's
        # Schedule A shape, which must not be read as debts.
        after = FakePage(
            [
                word("SP", OWNER_X, 20.0),
                word("Somebody", CREDITOR_X, 20.0),
                word("$1,001", AMOUNT_X, 20.0),
                word("-", AMOUNT_X + 40, 20.0),
                word("$15,000", AMOUNT_X, 30.0),
            ],
            band_rects((15.0, 45.0)),
        )
        debts = DisclosureParser()._liabilities_from_row_bands(FakePdf(debt_page(), after))
        assert [d["creditor"] for d in debts] == ["Town & Country Bank"]

    def test_bands_report_whether_the_schedule_is_still_open(self):
        _, still_open = _schedule_bands(debt_page(), "D")
        assert still_open is None
