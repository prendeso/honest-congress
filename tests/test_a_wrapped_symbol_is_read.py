"""The symbol on the wrapped half of an asset name belongs to the row above it.

A House PTR wraps a long asset name onto a second line, and the symbol is
usually on that second line:

    SP NVIDIA Corporation - Common Stock P 11/07/2024 08/11/2025 $100,001 -
    (NVDA) [ST] $250,000

The two recovery paths that read a row off a single printed line -- a table
cell pdfplumber collapsed, and a printed row no table yielded -- never looked at
the line below. In one filing 279 of 722 rows were stored with no ticker,
NVIDIA, Alphabet and Amazon among them, and every detector that joins on a
ticker missed those trades. Over the 90 PTRs an audit downloaded, the fix adds a
ticker to 1,085 rows (2,056 -> 3,141) and changes no ticker and no other field.
"""

from __future__ import annotations

from decimal import Decimal

from src.parsing.ptr_parser import ParseQuality, PTRParser

NVIDIA = (
    "SP NVIDIA Corporation - Common Stock P 11/07/2024 08/11/2025 $100,001 -\n"
    "(NVDA) [ST] $250,000\n"
    "F S : New"
)
ALPHABET_SALE = (
    "SP Alphabet Inc. - Class C Capital Stock S (partial) 06/10/2025 08/13/2025 "
    "$1,001 - $15,000\n(GOOG) [ST]\nF S : New"
)


def test_a_collapsed_cell_takes_its_symbol_from_the_wrapped_line():
    txn = PTRParser()._parse_collapsed_cell(NVIDIA)

    assert txn["ticker"] == "NVDA"
    assert txn["description"] == "NVIDIA Corporation - Common Stock\n(NVDA) [ST]"
    assert (txn["amount_min"], txn["amount_max"]) == (Decimal("100001"), Decimal("250000"))


def test_the_type_is_not_left_in_the_name():
    txn = PTRParser()._parse_collapsed_cell(ALPHABET_SALE)

    assert txn["ticker"] == "GOOG"
    assert "(partial)" not in txn["description"]
    assert txn["transaction_type"] == "sale"


def test_a_footnote_under_the_row_is_not_its_name():
    txn = PTRParser()._parse_collapsed_cell(
        "Acme Holdings Common Stock P 01/02/2025 01/20/2025 $1,001 - $15,000\nF S : New"
    )
    assert txn["description"] == "Acme Holdings Common Stock"


def test_the_next_row_is_not_this_rows_continuation():
    lines = [
        "SP Acme Holdings P 01/02/2025 01/20/2025 $1,001 - $15,000",
        "SP NVIDIA Corporation - Common Stock P 11/07/2024 08/11/2025 $1,001 - $15,000",
        "(NVDA) [ST]",
    ]
    assert PTRParser()._continuation(lines, 0) == ""
    assert PTRParser()._continuation(lines, 1) == "(NVDA) [ST]"


def test_a_printed_row_no_table_yielded_takes_its_symbol_too():
    text = (
        "SP Amazon.com, Inc. - Common Stock S (partial) 05/01/2025 06/01/2025 $1,001 - $15,000\n"
        "(AMZN) [ST]\nF S : New"
    )
    (txn,) = PTRParser()._recover_printed_rows(text, [], ParseQuality())

    assert txn["ticker"] == "AMZN"
    assert txn["description"] == "Amazon.com, Inc. - Common Stock\n(AMZN) [ST]"
