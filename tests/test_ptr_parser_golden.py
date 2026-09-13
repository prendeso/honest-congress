"""The PTR parser, pinned to real House Clerk filings.

Fixtures in `tests/fixtures/ptr/` are the pdfplumber-extracted tables from
actual 2024 filings, captured with the expectations read out of the same
documents. Synthetic XML would not have caught any of what these do, because
every bug here came from the real layout:

* PTR tables carry TWO date columns -- "Date" (the trade) and "Notification
  Date" (when the filer was told). A `"date" in header` test matched both, and
  the notification column came second, so it won. Measured across 18 real
  filings, 29 of 31 transaction dates were the notification date. STOCK Act
  compliance is filing date minus transaction date, so this silently understated
  every members' lateness.
* A footnote row follows each transaction -- "Filing Status: New", with glyphs
  rendered as NUL bytes. It carries a non-empty description, so it passed the
  emptiness check and was emitted as a transaction with every field None: 46% of
  all rows the parser produced.
* Amount ranges wrap across lines ("$15,001 -" then "$50,000"), so the
  line-at-a-time text fallback stored no amount at all.
* Filings tag holdings with a bracketed asset-class code -- [ST] stock, [CS]
  common stock. The ticker extractor read those as tickers, so a TuHURA
  Biosciences purchase was recorded against a company called "CS".
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.parsing.ptr_parser import ASSET_CLASS_CODES, PTRParser

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "ptr").glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture(params=FIXTURES, ids=lambda p: p.stem)
def filing(request) -> dict:
    return _load(request.param)


@pytest.fixture
def parser() -> PTRParser:
    return PTRParser()


def _parse_tables(parser: PTRParser, filing: dict) -> list[dict]:
    """Run the table path over a fixture's captured tables."""
    out = []
    for table in filing["tables"]:
        if not table:
            continue
        cols = parser._identify_columns(table[0])
        for row in table[1:]:
            parsed = parser._parse_table_row(row, cols)
            if parsed:
                out.append(parsed)
    return out


class TestGoldenFilings:
    def test_fixtures_exist(self):
        assert FIXTURES, "golden fixtures are missing; capture them from House Clerk"

    def test_emits_no_phantom_rows(self, parser, filing):
        """Every emitted row must carry something real."""
        for txn in _parse_tables(parser, filing):
            assert not (
                txn.get("transaction_date") is None
                and txn.get("amount_min") is None
                and txn.get("amount_max") is None
            ), f"phantom row from {filing['document_id']}: {txn}"

    def test_transaction_count_matches_the_filing(self, parser, filing):
        expected = filing["expected_transactions"]
        if not expected:
            pytest.skip("text-fallback filing; the table path yields nothing")

        assert len(_parse_tables(parser, filing)) == len(expected)

    def test_rows_collapsed_into_one_cell_are_recovered(self, parser, filing):
        """pdfplumber sometimes jams a whole record into the first cell.

        Read by column index that looks like an empty row, and it used to be
        dropped: across these six filings 18 transactions were lost that way
        against 16 kept, and two filings parsed to nothing while being recorded
        as parsed successfully.
        """
        expected_recovered = [
            e for e in filing["expected_transactions"] if e.get("recovered_from_collapsed_row")
        ]
        if not expected_recovered:
            pytest.skip("no collapsed rows in this filing")

        parsed = _parse_tables(parser, filing)
        got = [t for t in parsed if t.get("recovered_from_collapsed_row")]
        assert len(got) == len(expected_recovered)

    def test_a_recovered_row_is_not_folded_into_its_neighbour(self, parser, filing):
        """A collapsed row and a normal row for the same asset are two trades.

        In 20023805 the filing reports seven Myno Carbon Corp transactions on
        seven different dates. Deduplicating by description would collapse them
        into one and lose six.
        """
        parsed = _parse_tables(parser, filing)
        dates = [t["transaction_date"] for t in parsed if t["transaction_date"]]
        assert len(parsed) == len(filing["expected_transactions"])
        assert len(dates) == len(filing["expected_transactions"]), (
            "every recovered row must carry its own date"
        )

    def test_records_the_trade_date_not_the_notification_date(self, parser, filing):
        expected = filing["expected_transactions"]
        if not expected:
            pytest.skip("text-fallback filing")

        parsed = _parse_tables(parser, filing)
        for txn, want in zip(parsed, expected, strict=True):
            got = txn["transaction_date"]
            assert got is not None
            assert got.strftime("%m/%d/%Y") == want["date"], (
                f"{filing['document_id']}: recorded {got:%m/%d/%Y}, "
                f"filing says {want['date']} (notification {want['notification']})"
            )

    def test_notification_date_is_kept_separately(self, parser, filing):
        expected = filing["expected_transactions"]
        if not expected or not expected[0]["notification"]:
            pytest.skip("no notification date in this filing")

        parsed = _parse_tables(parser, filing)
        for txn, want in zip(parsed, expected, strict=True):
            if txn.get("notification_date") and want["notification"]:
                assert txn["notification_date"].strftime("%m/%d/%Y") == want["notification"]

    def test_amounts_are_extracted(self, parser, filing):
        expected = filing["expected_transactions"]
        if not expected:
            pytest.skip("text-fallback filing")

        parsed = _parse_tables(parser, filing)
        for txn, want in zip(parsed, expected, strict=True):
            if want["amount"] is None:  # pragma: no cover - none left in the corpus
                # Two records in the corpus have an asset name that wraps across
                # three lines, which interleaves it with the amount column and
                # leaves no legible band. The parser must return nothing rather
                # than assemble a figure out of the wreckage.
                assert txn["amount_min"] is None and txn["amount_max"] is None, (
                    f"{filing['document_id']}: invented an amount the filing does not show"
                )
                continue
            if "$" in want["amount"]:
                assert txn["amount_min"] is not None or txn["amount_max"] is not None, (
                    f"{filing['document_id']}: no amount for {want['amount']!r}"
                )

    def test_the_direction_matches_the_filing(self, parser, filing):
        """A sale recorded as a purchase is worse than a dropped row.

        Two ways this went wrong on real filings, both fixed by reading the
        type from where the form puts it rather than scanning for keywords:

        * "Best Buy Co., Inc. Common Stock S 02/23/2024" parsed as a PURCHASE,
          because "Buy" is in the company name.
        * The Transaction Type cell "S (partial)" parsed as a PURCHASE, because
          a substring test found the "p" inside "partial".

        Direction is not cosmetic: `contract_front_run` only looks at purchases,
        and the cross-member cluster detector groups by it.
        """
        codes = {"P": "purchase", "S": "sale", "E": "exchange"}
        for txn, want in zip(
            _parse_tables(parser, filing), filing["expected_transactions"], strict=True
        ):
            expected = codes.get((want["type"] or "")[:1])
            if expected:
                assert txn["transaction_type"] == expected, (
                    f"{filing['document_id']}: filing says {want['type']}, "
                    f"parsed {txn['transaction_type']} for {txn['description'][:40]!r}"
                )

    def test_a_company_name_containing_a_keyword_survives(self, parser):
        """ "Best Buy" must keep the word Buy.

        The description used to have every transaction keyword stripped from it
        wherever it appeared, so "Best Buy Co., Inc." became "Best Co., Inc."
        and "Purchase Point Media Corp" became "Point Media Corp" -- a company
        name mangled by a word it happens to contain, in a field that feeds
        sector classification and the opacity index.
        """
        got = parser._parse_text_line(
            "Best Buy Co., Inc. Common Stock S 02/23/2024 $1,001 - $15,000"
        )
        assert got["description"] == "Best Buy Co., Inc. Common Stock"
        assert got["transaction_type"] == "sale"

        got = parser._parse_text_line("Purchase Point Media Corp S 01/02/2024 $1,001 - $15,000")
        assert got["description"] == "Purchase Point Media Corp"
        assert got["transaction_type"] == "sale"

    def test_no_asset_class_code_is_stored_as_a_ticker(self, parser, filing):
        for txn in _parse_tables(parser, filing):
            ticker = txn.get("ticker")
            if ticker:
                assert ticker not in ASSET_CLASS_CODES, (
                    f"{filing['document_id']}: [{ticker}] is an asset-class code, not a ticker"
                )

    def test_dates_are_not_in_the_future(self, parser, filing):
        for txn in _parse_tables(parser, filing):
            if txn.get("transaction_date"):
                assert txn["transaction_date"] <= datetime.now()


class TestWrappedAmounts:
    """Amount ranges wrap across lines in the text fallback."""

    def test_rejoins_a_split_range(self, parser):
        lines = ["Apple Inc. - Common Stock (AAPL) S 10/07/2024 10/08/2024 $15,001 -", "$50,000"]

        joined = parser._join_wrapped_amounts(lines)

        assert len(joined) == 1
        assert "$15,001 - $50,000" in joined[0]

    def test_strips_a_wrapped_asset_class_tag(self, parser):
        lines = ["Something (X) S 10/07/2024 $15,001 -", "[ST] $50,000"]

        joined = parser._join_wrapped_amounts(lines)

        assert "$15,001 - $50,000" in joined[0]

    def test_leaves_complete_lines_alone(self, parser):
        lines = ["Apple (AAPL) S 10/07/2024 $15,001 - $50,000", "other text"]

        assert parser._join_wrapped_amounts(lines) == lines

    def test_dangling_range_at_end_of_input_is_kept(self, parser):
        lines = ["Apple (AAPL) S 10/07/2024 $15,001 -"]

        assert parser._join_wrapped_amounts(lines) == lines


class TestTickerExtraction:
    @pytest.mark.parametrize("code", ["ST", "CS", "OP", "MF"])
    def test_bracketed_asset_class_codes_are_not_tickers(self, parser, code):
        assert parser._extract_ticker(f"TuHURA Biosciences, Inc. [{code}]") is None

    def test_parenthesised_tickers_are_still_read(self, parser):
        assert parser._extract_ticker("Apple Inc. - Common Stock (AAPL)") == "AAPL"

    def test_real_ticker_wins_over_a_trailing_class_code(self, parser):
        assert parser._extract_ticker("Apple Inc. (AAPL) [ST]") == "AAPL"
