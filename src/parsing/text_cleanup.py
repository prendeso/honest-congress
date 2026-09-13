"""Make text pulled out of a PDF safe to store.

PostgreSQL rejects a NUL (0x00) byte anywhere in a `text` or `varchar` value --
not as a constraint violation but as a `psycopg.DataError` raised at flush time,
which poisons the whole transaction. SQLite accepts NUL happily, so nothing in
the test suite or in local development ever showed it.

pdfplumber emits NUL when a page's font carries a broken or absent ToUnicode
map: each glyph it cannot map to a character comes back as U+0000. One 2024
House filing rendered a section heading as `D` followed by ten NULs, and that
single row aborted a parse run of a thousand filings.

Stripping is deliberate rather than replacing with U+FFFD: the replacement
character would be stored, served and displayed, turning an unmappable glyph
into visible mojibake on the site. What survives -- `D: Corporate Bond` -- is
short of the truth but readable, and the filing's confidence score is what
records that the read was imperfect.
"""

from typing import Any, List, Sequence


def clean_text(value: str | None) -> str:
    """Return `value` with NUL bytes removed."""
    if not value:
        return ""
    return value.replace("\x00", "")


def clean_cell(value: Any) -> Any:
    """Clean one extracted table cell, leaving non-strings (and None) alone."""
    if isinstance(value, str):
        return clean_text(value)
    return value


def clean_tables(tables: Sequence[Sequence[Sequence[Any]]]) -> List[List[List[Any]]]:
    """Clean every cell of every row of every extracted table.

    Table cells reach the database as asset descriptions and tickers just as
    page text does, so sanitising only `extract_text` would leave the same
    failure reachable through the table path.
    """
    return [[[clean_cell(cell) for cell in row] for row in table] for table in tables]
