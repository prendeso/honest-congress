"""Four ways a printed row was misread, each found on a real House PTR.

* "S (partial)" between the type and the date defeated the anchor that finds
  the trade date, so the first date on the line -- a Treasury bill's maturity,
  printed in its name -- was stored as the trade date, and the row was then
  recovered a second time with no amount. David Trone's PTR 20024730: 30 rows
  stored for 28 printed.
* Footnote prose wraps, and its second line ("1/16/26.") carries a date and no
  label, so it was counted as an unread transaction (20033725: 20 "detected",
  18 real).
* A row read off a printed line ignored an amount that wrapped: "$15,001 -"
  here, "Stock (AMAT) [ST] $50,000" below, stored with no amount at all.
* An exact figure -- "$172.00", which a custodial account reports -- had its
  cents read as a second number and became a range from $0 to $172.

Over the 90 PTRs an audit downloaded: rows with no amount 24 -> 3, and no
ticker, date, direction or owner changed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.parsing.ptr_parser import ParseQuality, PTRParser


def test_a_partial_sale_is_dated_by_the_trade_not_the_maturity():
    txn = PTRParser()._parse_collapsed_cell(
        "JT US Treasury Bill 12/21/2023 [GS] S (partial) 10/17/2023 03/25/2024 $100,001 -\n"
        "$250,000\nF S : New"
    )
    assert txn["transaction_date"] == datetime(2023, 10, 17)
    assert txn["notification_date"] == datetime(2024, 3, 25)
    assert txn["transaction_type"] == "sale"
    assert (txn["amount_min"], txn["amount_max"]) == (Decimal("100001"), Decimal("250000"))
    assert txn["description"] == "US Treasury Bill 12/21/2023 [GS]"


def test_wrapped_footnote_prose_is_not_a_transaction():
    row = [
        "",
        "",
        "F S: New\nD: Exercised 50 call options purchased 1/14/25 at a strike of $150 "
        "with an expiration date of\n1/16/26.",
    ]
    assert PTRParser._is_candidate_row(row) is False


def test_a_record_line_is_still_a_candidate():
    row = ["Acme Corp (ACME) [ST]", "P", "01/02/2025", "01/20/2025", "$1,001 - $15,000"]
    assert PTRParser._is_candidate_row(row) is True


def test_a_printed_row_closes_an_amount_that_wrapped():
    text = (
        "Applied Materials, Inc. - Common P 03/13/2025 03/28/2025 $15,001 -\n"
        "Stock (AMAT) [ST] $50,000\nF S : New"
    )
    (txn,) = PTRParser()._recover_printed_rows(text, [], ParseQuality())

    assert (txn["amount_min"], txn["amount_max"]) == (Decimal("15001"), Decimal("50000"))
    assert txn["ticker"] == "AMAT"


def test_the_symbol_can_sit_on_the_amount_line_and_the_tag_below_it():
    text = (
        "International Business Machines P 04/14/2026 05/07/2026 $15,001 -\n"
        "Corporation Common Stock (IBM) $50,000\n[ST]\nF S : New"
    )
    (txn,) = PTRParser()._recover_printed_rows(text, [], ParseQuality())

    assert txn["ticker"] == "IBM"
    assert txn["amount_max"] == Decimal("50000")


def test_an_exact_figure_keeps_its_cents():
    parser = PTRParser()
    assert parser._parse_amount_range("$172.00") == (Decimal("172.00"), Decimal("172.00"))
    txn = parser._parse_text_line("DC AT&T Inc. (T) [ST] P 03/04/2024 03/04/2024 $223.60")
    assert (txn["amount_min"], txn["amount_max"]) == (Decimal("223.60"), Decimal("223.60"))


def test_bands_are_unchanged():
    parser = PTRParser()
    assert parser._parse_amount_range("$1,001 - $15,000") == (Decimal("1001"), Decimal("15000"))
    assert parser._parse_amount_range("Over $1,000,000") == (Decimal("1000001"), None)
