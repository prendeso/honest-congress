"""A Senate annual filing can hold more than one transaction table.

`_transaction_table` returned the FIRST table whose joined header text held
"amount" and ("transaction" or "date"), and stopped. Three things were wrong
with that, and each is pinned below.

**It dropped tables.** A Senate annual report carries Part 4a "Periodic
Transaction Report Summary" and Part 4b "Transactions" as separate tables with
different column orders. Measured over 81 live annual filings: 1,087 of 2,615
transactions missing — 41.6% of the corrected total. 23 filings change, and 19
of them were stored *partial at confidence 1.0 with no warning*, because a table
nobody read contributes to neither side of `rows_parsed / rows_detected`.

**It picked the wrong table.** Part 1 "Honoraria Payments" has an amount and a
date and no asset, so it satisfied the old predicate — and it precedes Part 4 in
document order, so on a filing that had one it was the table selected. Every row
then failed the `if not description: continue` guard, and the filing parsed to
zero transactions reporting "no transactions found in a periodic transaction
report". 8 of the 81 filings carried honoraria; the 4 that also held real
transaction tables stored nothing, losing 67 disclosed trades.

**Its counters could not survive a second table.** `rows_parsed = len(...)` is an
assignment, so the last table would win.

Live confirmation after the fix, against eFD today:
  Rick Scott CY2024 annual  90 -> 138 transactions, confidence 1.0, no warnings
  Katie Britt annual         0 ->  12 transactions, confidence 1.0, no warnings
"""

from __future__ import annotations

import pytest

from src.parsing.confidence import score_ptr_parse
from src.parsing.senate_html_parser import SenateHtmlParser

# Header rows copied from the survey of 81 live annual filings, so a test that
# passes here is a test against what eFD actually serves.
PART_4A = [
    "",
    "#",
    "Transaction Date",
    "Owner",
    "Ticker",
    "Asset Name",
    "Type",
    "Amount",
    "Comment",
]
PART_4B = [
    "",
    "#",
    "Owner",
    "Ticker",
    "Asset Name",
    "Transaction Type",
    "Transaction Date",
    "Amount",
    "Comments",
]
PART_1_HONORARIA = [
    "",
    "#",
    "Date",
    "Activity",
    "Amount",
    "Who Paid?",
    "Who received payment?",
    "Comments",
]
PART_3_ASSETS = ["", "Asset", "Asset Type", "Owner", "Value", "Income Type", "Income"]
PART_7_LIABILITIES = [
    "",
    "#",
    "Incurred",
    "Debtor",
    "Type",
    "Points",
    "Rate(Term)",
    "Amount",
    "Creditor",
    "Comments",
]
PART_5_GIFTS = ["", "#", "Date", "Source", "Description", "Value", "Comments"]


def table(headers, rows):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def filing(*tables):
    return f"<html><body>{''.join(tables)}</body></html>"


def a_4a_row(n, ticker="AAPL"):
    return [
        "",
        str(n),
        "01/10/2024",
        "Self",
        ticker,
        f"Apple Inc {n}",
        "Purchase",
        "$1,001 - $15,000",
        "--",
    ]


def a_4b_row(n, ticker="MSFT"):
    return [
        "",
        str(n),
        "Spouse",
        ticker,
        f"Microsoft Corp {n}",
        "Sale (Full)",
        "03/15/2024",
        "$15,001 - $50,000",
        "n/a",
    ]


def parse(markup, tmp_path, name="f.html"):
    p = tmp_path / name
    p.write_text(markup, encoding="utf-8")
    return SenateHtmlParser().parse_senate_html(str(p))


class TestEveryTransactionTableIsRead:
    def test_both_parts_of_an_annual_filing_are_read(self, tmp_path):
        markup = filing(
            table(PART_3_ASSETS, [["", "Residence", "Business Entity", "Joint", "--", "", ""]]),
            table(PART_4A, [a_4a_row(i) for i in range(1, 91)]),
            table(PART_4B, [a_4b_row(i) for i in range(1, 49)]),
        )

        result = parse(markup, tmp_path)

        assert len(result["transactions"]) == 138, (
            "only the first transaction table was read — the second is dropped "
            "silently, which is 48 of this filing's 138 disclosed trades"
        )

    def test_the_two_tables_column_orders_are_both_honoured(self, tmp_path):
        """4a and 4b order their columns differently. A single column map
        derived once and reused would read 4b through 4a's positions."""
        markup = filing(
            table(PART_4A, [a_4a_row(1, ticker="AAPL")]),
            table(PART_4B, [a_4b_row(1, ticker="MSFT")]),
        )

        txns = parse(markup, tmp_path)["transactions"]

        by_ticker = {t["ticker"]: t for t in txns}
        assert set(by_ticker) == {"AAPL", "MSFT"}
        assert by_ticker["AAPL"]["transaction_type"] == "purchase"
        assert by_ticker["MSFT"]["transaction_type"] == "sale"
        assert by_ticker["MSFT"]["owner"] == "Spouse"

    def test_the_counters_total_every_table(self, tmp_path):
        markup = filing(
            table(PART_4A, [a_4a_row(i) for i in range(1, 91)]),
            table(PART_4B, [a_4b_row(i) for i in range(1, 49)]),
        )

        result = parse(markup, tmp_path)
        quality = result["quality"]

        assert quality["rows_detected"] == 138
        assert quality["rows_parsed"] == 138

    def test_a_complete_read_of_two_tables_still_scores_one(self, tmp_path):
        """`rows_parsed = len(...)` made the last table win, so a filing read
        perfectly scored 48/138 = 0.35 and carried "90 row(s) looked like
        transactions but could not be read" — and stayed in the
        `--min-confidence 1.0` re-parse queue for ever."""
        markup = filing(
            table(PART_4A, [a_4a_row(i) for i in range(1, 91)]),
            table(PART_4B, [a_4b_row(i) for i in range(1, 49)]),
        )

        result = parse(markup, tmp_path)
        score = score_ptr_parse(result["quality"], result["transactions"], None)

        assert score.confidence == 1.0
        assert score.warnings == []


class TestHonorariaIsNotATransactionTable:
    def test_a_filing_led_by_honoraria_still_yields_its_trades(self, tmp_path):
        """Part 1 precedes Part 4, so the first-match rule selected it and the
        filing parsed to zero. Four live filings were storing nothing."""
        markup = filing(
            table(
                PART_1_HONORARIA,
                [
                    ["", "1", "05/02/2024", "Speech", "$5,000", "A University", "Charity", "n/a"],
                ],
            ),
            table(PART_4B, [a_4b_row(i) for i in range(1, 13)]),
        )

        result = parse(markup, tmp_path)

        assert len(result["transactions"]) == 12
        assert result["parse_errors"] == []

    def test_honoraria_rows_never_become_transactions(self, tmp_path):
        markup = filing(
            table(
                PART_1_HONORARIA,
                [
                    ["", "1", "05/02/2024", "Speech", "$5,000", "A University", "Charity", "n/a"],
                ],
            )
        )

        result = parse(markup, tmp_path)

        assert result["transactions"] == []
        assert result["quality"]["rows_detected"] == 0, (
            "an honoraria row counted against the parse's own denominator"
        )


class TestTheOtherPartsAreNotMistakenForTrades:
    @pytest.mark.parametrize(
        "name,headers,row",
        [
            ("assets", PART_3_ASSETS, ["", "Residence", "Business Entity", "Joint", "--", "", ""]),
            (
                "liabilities",
                PART_7_LIABILITIES,
                [
                    "",
                    "1",
                    "2024",
                    "Self",
                    "Loan",
                    "-",
                    "0%",
                    "$5,000,001 - $25,000,000",
                    "A Bank",
                    "n/a",
                ],
            ),
            ("gifts", PART_5_GIFTS, ["", "1", "01/02/2024", "A Donor", "A Gift", "$500", "n/a"]),
        ],
    )
    def test_a_non_transaction_part_yields_nothing(self, tmp_path, name, headers, row):
        result = parse(filing(table(headers, [row])), tmp_path, name=f"{name}.html")

        assert result["transactions"] == []

    def test_a_filing_of_only_other_parts_reports_an_unknown_layout(self, tmp_path):
        """Not a scan — it has a text layer. The distinction #56 introduced."""
        result = parse(filing(table(PART_3_ASSETS, [["", "A", "B", "C", "--", "", ""]])), tmp_path)

        assert result["quality"]["text_extracted"] is True
        assert "not a scan" in result["parse_errors"][0]


class TestASingleTableFilingIsUnchanged:
    """A Senate PTR has exactly one table. The fix must not move it."""

    def test_a_ptr_reads_the_same_as_before(self, tmp_path):
        markup = filing(table(PART_4A, [a_4a_row(i) for i in range(1, 11)]))

        result = parse(markup, tmp_path)
        score = score_ptr_parse(result["quality"], result["transactions"], None)

        assert len(result["transactions"]) == 10
        assert result["quality"]["rows_detected"] == 10
        assert result["quality"]["rows_parsed"] == 10
        assert score.confidence == 1.0
