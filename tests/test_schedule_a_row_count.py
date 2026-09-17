"""How many holdings the document names, counted independently of the parser.

`score_fd_parse` used to be binary -- 1.0 unless the parse produced nothing --
so it could only ever say "it ran". Scored against its own output, a parser that
loses half the form looks perfect, and that is exactly what happened: 927 of 927
House annual filings in the corpus report confidence 1.0 while capturing about
half of Schedule A.

The fix needs a signal the parser cannot influence. Every Schedule A holding on
the House annual form carries exactly one asset-class code in square brackets --
[BA] bank account, [ST] stock, [MF] mutual fund, [HE] hedge fund -- so counting
those in the text layer says how many rows the DOCUMENT holds, against however
many the parser managed to STORE.

The fixture below is the real text shape of Earl Carter's 2024 annual, document
10066714, including the two holdings that are in the text layer and in no table
at all. Run end to end against the live PDF the counter finds 44 and the parser
stores 22, so the filing now scores 0.5 rather than 1.0.
"""

from __future__ import annotations

from src.parsing.pdf_parser import count_schedule_a_rows

# `clean_text` reduces "SCHEDULE A: ASSETS AND "UNEARNED" INCOME" to this, which
# is why the counter accepts both spellings. Matching only the uppercase form
# found the region in the raw page text and never in `raw_text` -- the string it
# is actually called with -- so it detected zero rows and scored a perfect 1.0.
CARTER = """Filing ID #10066714
Name: Hon. Earl Leroy Carter
S A: A  "U" I
Asset Owner Value of Asset Income Type(s) Income Tx. >
Ameris Bank [BA] $1,000,001 - Interest $5,001 -
Guardian Point Capital [HE] $5,000,001 - Capital Gains, $1,000,001 -
Bank of the Ozarks [BA] None Interest $201 - $1,000
Nuveen Intermediate Duration Muni Bd 1 [MF] $100,001 - Dividends $1,001 -
Mass Mutual 401(k) => Cash Held [OT] $100,001 - Tax-Deferred
S B: T
Asset Owner Date Tx. Amount Cap.
Stifel => Ameris Bancorp - Common Stock (ABCB) [ST] 07/19/2024 S $100,001 -
Stifel => Ameris Bancorp - Common Stock (ABCB) [ST] 07/23/2024 S $100,001 -
S D: L
"""


class TestItCountsOnlyScheduleA:
    def test_it_counts_the_holdings(self):
        assert count_schedule_a_rows(CARTER) == 5

    def test_schedule_b_trades_are_not_holdings(self):
        """Schedule B carries the same [ST] codes. Counting the whole document
        would inflate the denominator and make a bad parse look worse than it
        is -- which is its own kind of dishonest."""
        assert "[ST]" in CARTER.split("S B: T")[1]
        assert count_schedule_a_rows(CARTER) == 5

    def test_it_counts_the_rows_that_reach_no_table(self):
        """Guardian Point and Ameris Bank appear in the text layer of the real
        filing and in none of its tables, so the parser stores neither. They are
        exactly what the denominator has to include for the score to be honest.
        """
        counted_without_them = count_schedule_a_rows(
            CARTER.replace("Ameris Bank [BA] $1,000,001 - Interest $5,001 -\n", "").replace(
                "Guardian Point Capital [HE] $5,000,001 - Capital Gains, $1,000,001 -\n", ""
            )
        )

        assert counted_without_them == 3
        assert count_schedule_a_rows(CARTER) == 5


class TestItRefusesToGuess:
    def test_no_text_counts_nothing(self):
        assert count_schedule_a_rows("") == 0

    def test_a_document_with_no_schedule_a_counts_nothing(self):
        """An extension request is one page and has no schedules. Counting
        stray codes would invent a denominator and score it as a failure."""
        assert count_schedule_a_rows("Filing ID #30021221\nExtension request\n") == 0

    def test_the_uppercase_spelling_also_works(self):
        raw = CARTER.replace('S A: A  "U" I', "SCHEDULE A: ASSETS").replace(
            "S B: T", "SCHEDULE B: TRANSACTIONS"
        )

        assert count_schedule_a_rows(raw) == 5
