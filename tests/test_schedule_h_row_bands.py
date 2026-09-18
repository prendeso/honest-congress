"""Reading Schedule H: the trips somebody else paid for.

Nothing in this project read Schedule H, "Travel Payments and Reimbursements".
Measured over a random sample of 70 House annual reports drawn from the Clerk's
2024-25 index (67 parsed): **35 of them disclose at least one trip, carrying 61
between them.** So these were in the documents and in no database --

    Norma Torres     American Israel Education Foundation, Inc. (AIEF)
                     San Francisco, CA - Tel Aviv
    Pramila Jayapal  Center for Democracy in the Americas
                     Washington, DC - Havana, Cuba
    Greg Murphy      The Aspen Institute
                     Raleigh, NC - Bellagio, Italy
    Greg Casar       Center for Economic and Policy Research
                     Austin, TX - Bogota, Colombia

-- against Schedules G (Gifts) and I (Payments to Charity), which print
"None disclosed." in all 67 filings of that same sample and would be empty
tables.

The form bands Schedule H exactly like A and D, so `_schedule_bands` reads it
with no new machinery. What makes it worth its own test file is the wrapping: a
sponsor name runs to three printed lines and a five-leg itinerary to five, and
all of it is one band, one trip.

THREE COLUMNS ARE READ AS HEADINGS AND THEN DROPPED. Lodging?, Food? and
Family? are drawn as vector curves rather than text -- page 6 of document
10074944 carries seven trips and zero characters right of x=400, where all three
sit. They are matched so `Days at Own Exp.` has a right edge, and no further.

Fixtures are hand-built pages rather than PDFs, for the reason
`test_schedule_a_row_bands` gives: what is under test is the reading of a
geometry, and a real PDF would test pdfplumber as much as this code.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.parsing.pdf_parser import DisclosureParser, _schedule_bands
from tests.test_schedule_a_row_bands import FakePage, FakePdf, band_rects, word

# Schedule H's column edges, taken from document 10074944.
SOURCE_X, START_X, END_X, ITIN_X, DAYS_X, LODGING_X, FOOD_X, FAMILY_X = (
    25.0,
    135.0,
    191.0,
    247.0,
    348.0,
    416.0,
    478.0,
    529.0,
)


def header_words(top: float) -> List[Dict[str, Any]]:
    """The Schedule H column header, as the form prints it.

    Two variants exist in the corpus -- this one and a "Start Date End Date"
    spelling. Both match the single shape in `_SCHEDULE_COLUMNS["H"]`, because
    `_header_columns` matches first words in order and may skip.
    """
    return [
        word("Source", SOURCE_X, top),
        word("Start", START_X, top),
        word("Date", START_X + 26, top),
        word("End", END_X, top),
        word("Date", END_X + 22, top),
        word("Itinerary", ITIN_X, top),
        word("Days", DAYS_X, top),
        word("at", DAYS_X + 26, top),
        word("Own", DAYS_X + 38, top),
        word("Lodging?", LODGING_X, top),
        word("Food?", FOOD_X, top),
        word("Family?", FAMILY_X, top),
    ]


def travel_page() -> FakePage:
    """Two trips: one whose sponsor and itinerary both wrap, one that does not."""
    words = header_words(252.0)
    words += [
        # Atlantic Council: sponsor over two lines, itinerary over four.
        word("Atlantic", SOURCE_X, 285.0),
        word("Council", SOURCE_X + 34, 285.0),
        word("United", SOURCE_X + 30, 296.0),
        word("States,", SOURCE_X + 58, 296.0),
        word("Inc.", SOURCE_X + 90, 296.0),
        word("06/13/2025", START_X, 285.0),
        word("06/19/2025", END_X, 285.0),
        word("San", ITIN_X, 285.0),
        word("Francisco,", ITIN_X + 17, 285.0),
        word("CA", ITIN_X + 60, 285.0),
        word("-", ITIN_X + 74, 285.0),
        word("Riyadh,", ITIN_X, 296.0),
        word("Saudi", ITIN_X + 33, 296.0),
        word("Arabia", ITIN_X + 58, 296.0),
        word("-", ITIN_X + 86, 296.0),
        word("Manama,", ITIN_X, 306.0),
        word("Bahrain", ITIN_X + 40, 306.0),
        word("0", DAYS_X + 27, 285.0),
        # Reaction Digital Media: one line.
        word("Reaction", SOURCE_X, 350.0),
        word("Digital", SOURCE_X + 38, 350.0),
        word("05/08/2025", START_X, 350.0),
        word("05/10/2025", END_X, 350.0),
        word("Washington,", ITIN_X, 350.0),
        word("DC", ITIN_X + 53, 350.0),
        word("-", ITIN_X + 68, 350.0),
        word("London,", ITIN_X, 360.0),
        word("UK", ITIN_X + 36, 360.0),
        word("3", DAYS_X + 27, 350.0),
        # Schedule I's heading is the only thing that says H has ended.
        word("S", SOURCE_X - 3, 666.0),
        word("I:", SOURCE_X + 9, 666.0),
        word("P", SOURCE_X + 27, 666.0),
    ]
    return FakePage(words, band_rects((277.0, 343.0), (343.0, 430.0), (430.0, 517.0)))


def trips(page: FakePage) -> List[Dict[str, Any]]:
    return DisclosureParser()._travel_from_row_bands(FakePdf(page))


class TestATripIsReadAsItsCells:
    def test_both_trips_are_found(self):
        assert len(trips(travel_page())) == 2

    def test_a_sponsor_that_wraps_is_one_sponsor(self):
        # The cost of getting this wrong is a trip filed under "Atlantic" and a
        # second phantom sponsor called "United States, Inc.".
        assert trips(travel_page())[0]["source"] == "Atlantic Council United States, Inc."

    def test_a_multi_leg_itinerary_is_one_itinerary(self):
        assert (
            trips(travel_page())[0]["itinerary"]
            == "San Francisco, CA - Riyadh, Saudi Arabia - Manama, Bahrain"
        )

    def test_the_dates_are_dates(self):
        first = trips(travel_page())[0]
        assert (first["start_date"].year, first["start_date"].month, first["start_date"].day) == (
            2025,
            6,
            13,
        )
        assert (first["end_date"].month, first["end_date"].day) == (6, 19)

    def test_days_at_own_expense_is_read_including_zero(self):
        # Zero is the answer that matters -- it means somebody else paid for all
        # of it -- so it must be stored as 0 and not lost to a falsy check.
        got = [t["days_at_own_expense"] for t in trips(travel_page())]
        assert got == [0, 3]

    def test_the_inclusion_flags_are_not_invented(self):
        # Lodging?, Food? and Family? cannot be read: they are drawn as vector
        # curves. Nothing here may claim to know them.
        for trip in trips(travel_page()):
            assert set(trip) == {
                "source",
                "start_date",
                "end_date",
                "itinerary",
                "days_at_own_expense",
            }


class TestWhatIsNotATrip:
    def test_a_band_with_no_date_is_not_a_trip(self):
        # The form's trailing note is a band of its own, inside the schedule and
        # above the next heading. Storing it puts a sponsor with no trip into
        # the table.
        page = travel_page()
        page._words = [w for w in page._words if w["top"] != 666.0]
        page._words += [
            word("None", SOURCE_X, 445.0),
            word("disclosed.", SOURCE_X + 30, 445.0),
            word("S", SOURCE_X - 3, 690.0),
            word("I:", SOURCE_X + 9, 690.0),
        ]
        assert len(trips(page)) == 2

    def test_a_band_with_a_date_and_no_sponsor_is_not_a_trip(self):
        page = travel_page()
        page._words = [w for w in page._words if not (w["top"] == 350.0 and w["x0"] < START_X)]
        assert [t["start_date"].month for t in trips(page)] == [6]

    def test_schedule_h_stops_at_the_next_schedule_heading(self):
        bands, _ = _schedule_bands(travel_page(), "H")
        assert [(top, bottom) for top, bottom, _ in bands] == [
            (277.0, 343.0),
            (343.0, 430.0),
            (430.0, 517.0),
        ]


class TestASchedulePageBreak:
    def test_a_trip_on_the_next_page_keeps_its_columns(self):
        # Schedule H runs to seven trips on document 10074944 and a long one
        # spills. A reader that needs a header on every page drops exactly the
        # rows that wrap most -- the multi-leg foreign trips.
        overflow = FakePage(
            [
                word("Yalta", SOURCE_X, 20.0),
                word("European", SOURCE_X + 25, 20.0),
                word("09/11/2025", START_X, 20.0),
                word("09/14/2025", END_X, 20.0),
                word("Washington,", ITIN_X, 20.0),
                word("DC", ITIN_X + 53, 20.0),
                word("-", ITIN_X + 68, 20.0),
                word("Kyiv,", ITIN_X, 30.0),
                word("Ukraine", ITIN_X + 26, 30.0),
                word("0", DAYS_X + 27, 20.0),
            ],
            band_rects((15.0, 60.0)),
        )
        page_one = travel_page()
        page_one._words = [w for w in page_one._words if w["top"] != 666.0]
        page_one.rects = band_rects((277.0, 343.0), (343.0, 430.0))

        found = DisclosureParser()._travel_from_row_bands(FakePdf(page_one, overflow))
        assert [t["source"] for t in found] == [
            "Atlantic Council United States, Inc.",
            "Reaction Digital",
            "Yalta European",
        ]
        assert found[2]["itinerary"] == "Washington, DC - Kyiv, Ukraine"

    def test_a_schedule_closed_before_the_page_ends_does_not_carry(self):
        # `travel_page` ends with Schedule I's heading, so nothing on the next
        # page belongs to H.
        after = FakePage(
            [
                word("Somebody", SOURCE_X, 20.0),
                word("01/02/2025", START_X, 20.0),
                word("Nowhere", ITIN_X, 20.0),
            ],
            band_rects((15.0, 60.0)),
        )
        found = DisclosureParser()._travel_from_row_bands(FakePdf(travel_page(), after))
        assert [t["source"] for t in found] == [
            "Atlantic Council United States, Inc.",
            "Reaction Digital",
        ]

    def test_bands_report_whether_the_schedule_is_still_open(self):
        _, still_open = _schedule_bands(travel_page(), "H")
        assert still_open is None
