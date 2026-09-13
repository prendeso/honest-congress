"""A NUL byte out of a PDF must never reach the database.

pdfplumber returns U+0000 for every glyph a page's font gives it no way to
map -- a missing or broken ToUnicode table. PostgreSQL refuses a NUL anywhere
in a text field, not with a constraint violation but with a `psycopg.DataError`
at flush time, which poisons the whole transaction.

SQLite stores NUL without complaint, which is why the entire test suite and
every local run was silent about it while a production parse died on the
first affected filing:

    (psycopg.DataError) PostgreSQL text fields cannot contain NUL (0x00) bytes
    ... 'description': 'D\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00: Corporate Bond'

So these tests assert on the parser output rather than on a stored row: that
is the boundary where the bytes are still ours to reject, and it is the one
place a SQLite-backed test can still see the difference.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from src.parsing.pdf_parser import DisclosureParser
from src.parsing.ptr_parser import PTRParser
from src.parsing.text_cleanup import clean_tables, clean_text

# What the broken filing actually looked like: a readable first glyph, then a
# run of characters the font could not map.
CORRUPTED = "D\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00: Corporate Bond"


class _FakePage:
    def __init__(self, text: str, tables: List[List[List[Any]]]):
        self._text = text
        self._tables = tables

    def extract_text(self) -> str:
        return self._text

    def extract_tables(self) -> List[List[List[Any]]]:
        return self._tables


class _FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def corrupted_pdf(monkeypatch):
    """Patch pdfplumber in both parser modules to yield NUL-bearing pages."""
    page = _FakePage(
        text=(
            "MEMBER NAME: Jane Doe\n"
            f"{CORRUPTED}\n"
            "Apple Inc\x00 (AAPL) [ST] 01/15/2024 P $1,001 - $15,000\n"
        ),
        tables=[
            [
                ["Asset", "Transaction Date", "Type", "Amount"],
                [CORRUPTED, "01/15/2024", "P", "$1,001 -\x00 $15,000"],
            ]
        ],
    )

    def fake_open(path, *args, **kwargs):
        return _FakePDF([page])

    import src.parsing.pdf_parser as pdf_module
    import src.parsing.ptr_parser as ptr_module

    monkeypatch.setattr(pdf_module.pdfplumber, "open", fake_open)
    monkeypatch.setattr(ptr_module.pdfplumber, "open", fake_open)


def _every_string(value: Any) -> List[str]:
    """Walk a parsed result and yield every string anywhere inside it."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: List[str] = []
        for key, item in value.items():
            found.extend(_every_string(key))
            found.extend(_every_string(item))
        return found
    if isinstance(value, (list, tuple, set)):
        found = []
        for item in value:
            found.extend(_every_string(item))
        return found
    return []


class TestTheSanitiserItself:
    def test_nuls_are_removed_not_replaced(self):
        assert clean_text(CORRUPTED) == "D: Corporate Bond"

    def test_none_and_empty_become_empty_string(self):
        # `extract_text()` returns None for a page with no text layer, and the
        # callers relied on `or ""` to absorb that.
        assert clean_text(None) == ""
        assert clean_text("") == ""

    def test_ordinary_text_is_untouched(self):
        assert clean_text("Berkshire Hathaway Inc. New Common Stock (BRK.B)") == (
            "Berkshire Hathaway Inc. New Common Stock (BRK.B)"
        )

    def test_non_string_cells_survive_intact(self):
        # pdfplumber leaves a cell it read nothing from as None, and a table
        # walker that assumed str would crash on it.
        assert clean_tables([[["a\x00b", None]]]) == [[["ab", None]]]


class TestNoNulReachesTheParserOutput:
    def test_annual_disclosure_parse(self, corrupted_pdf):
        result = DisclosureParser().parse_pdf("ignored.pdf")

        offenders = [s for s in _every_string(result) if "\x00" in s]
        assert not offenders, f"NUL bytes survived into the parsed filing: {offenders!r}"

    def test_periodic_transaction_report_parse(self, corrupted_pdf):
        result = PTRParser().parse_ptr("ignored.pdf")

        offenders = [s for s in _every_string(result) if "\x00" in s]
        assert not offenders, f"NUL bytes survived into the parsed PTR: {offenders!r}"

    def test_the_filing_is_still_read_rather_than_discarded(self, corrupted_pdf):
        """Stripping must not be a way of throwing the page away.

        If this passed on an empty result the tests above would pass on one
        too, and the fix would look right while reading nothing.
        """
        result = PTRParser().parse_ptr("ignored.pdf")

        assert result["quality"]["text_extracted"] is True
        assert result["transactions"], "the corrupted page yielded no transactions at all"
