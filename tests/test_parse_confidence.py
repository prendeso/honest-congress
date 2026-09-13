"""Parse confidence: how much of a filing the parser actually read.

Every trade in the system arrives through the PDF parser, and a disclosure was
marked `parsed = True` on any run that did not raise -- including one that
extracted nothing at all. These tests are mostly about the difference between
"we ran the parser" and "the parse worked".

The score is deliberately a completeness ratio rather than a weighted
judgement, so most of what is pinned here is arithmetic: a dropped row lowers it
by counting, a missing amount lowers it by counting. The three caps are asserted
and each has a test naming it as such.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.parsing.confidence import (
    NO_HEADER_CEILING,
    TEXT_FALLBACK_CEILING,
    score_fd_parse,
    score_ptr_parse,
)
from src.parsing.ptr_parser import ParseQuality, PTRParser

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "ptr").glob("*.json"))


def _quality(**overrides):
    base = {
        "rows_detected": 1,
        "rows_parsed": 1,
        "rows_recovered": 0,
        "text_extracted": True,
        "tables_found": True,
        "headers_recognised": True,
        "used_text_fallback": False,
    }
    base.update(overrides)
    return base


def _txn(**overrides):
    base = {
        "description": "Apple Inc. Common Stock",
        "transaction_type": "purchase",
        "transaction_date": datetime(2024, 3, 1),
        "amount_min": Decimal("1001"),
        "amount_max": Decimal("15000"),
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The ratio
# --------------------------------------------------------------------------


def test_a_complete_parse_scores_one():
    assert score_ptr_parse(_quality(), [_txn()]).confidence == 1.0


def test_a_dropped_row_lowers_the_score_by_arithmetic():
    # Four rows looked like transactions, three were read. No rule to tune.
    score = score_ptr_parse(_quality(rows_detected=4, rows_parsed=3), [_txn()] * 3)
    assert score.confidence == pytest.approx(0.75)
    assert any("could not be read" in w for w in score.warnings)


def test_a_missing_field_lowers_the_score_by_arithmetic():
    # One of four required fields absent on the only transaction.
    score = score_ptr_parse(_quality(), [_txn(amount_min=None)])
    assert score.confidence == pytest.approx(0.75)
    assert any("missing amount_min" in w for w in score.warnings)


def test_the_two_factors_compound():
    score = score_ptr_parse(
        _quality(rows_detected=4, rows_parsed=2), [_txn(), _txn(transaction_date=None)]
    )
    # 2/4 rows read, 7 of 8 required fields present.
    assert score.confidence == pytest.approx(0.5 * 7 / 8)


def test_a_missing_ticker_is_not_held_against_the_parser():
    # Plenty of disclosed assets legitimately have no ticker. Counting its
    # absence would punish the parser for the filing's own content.
    assert score_ptr_parse(_quality(), [_txn(ticker=None)]).confidence == 1.0


def test_an_amount_of_zero_counts_as_extracted():
    # A plain truthiness test would read Decimal("0") as missing.
    assert score_ptr_parse(_quality(), [_txn(amount_min=Decimal("0"))]).confidence == 1.0


def test_an_empty_description_does_not_count_as_extracted():
    assert score_ptr_parse(_quality(), [_txn(description="   ")]).confidence < 1.0


# --------------------------------------------------------------------------
# The three asserted caps
# --------------------------------------------------------------------------


def test_a_scanned_pdf_scores_zero():
    score = score_ptr_parse(_quality(text_extracted=False), [])
    assert score.confidence == 0.0
    assert "no text layer" in score.summary


def test_a_ptr_with_no_transactions_scores_zero():
    # A PTR exists to report transactions. This was previously recorded as a
    # clean success, which is how two filings in the corpus came to hold
    # nothing without anyone noticing.
    score = score_ptr_parse(_quality(rows_detected=3, rows_parsed=0), [])
    assert score.confidence == 0.0
    assert "no transactions found" in score.summary


def test_the_text_fallback_is_capped_below_a_clean_table_parse():
    # It can look complete and still be the weaker reading: that path has no
    # column structure to check itself against.
    score = score_ptr_parse(_quality(used_text_fallback=True), [_txn()])
    assert score.confidence == TEXT_FALLBACK_CEILING
    assert any("text layer" in w for w in score.warnings)


def test_guessed_column_positions_are_capped_lower_still():
    score = score_ptr_parse(_quality(headers_recognised=False), [_txn()])
    assert score.confidence == NO_HEADER_CEILING
    assert any("column positions assumed" in w for w in score.warnings)


# --------------------------------------------------------------------------
# Contradictions that mean a field came from the wrong place
# --------------------------------------------------------------------------


def test_a_trade_dated_after_its_own_filing_is_flagged():
    # Impossible, and the exact shape the notification-date bug took: the
    # parser was reading the column beside the one it wanted, so dates came out
    # later than they should have been.
    score = score_ptr_parse(
        _quality(), [_txn(transaction_date=datetime(2024, 6, 1))], filing_date=datetime(2024, 3, 1)
    )
    assert any("dated after the filing" in w for w in score.warnings)


def test_a_trade_before_its_filing_is_not_flagged():
    score = score_ptr_parse(
        _quality(), [_txn(transaction_date=datetime(2024, 1, 1))], filing_date=datetime(2024, 3, 1)
    )
    assert not any("dated after" in w for w in score.warnings)


def test_an_inverted_amount_band_is_flagged():
    score = score_ptr_parse(
        _quality(), [_txn(amount_min=Decimal("50000"), amount_max=Decimal("1001"))]
    )
    assert any("inverted amount band" in w for w in score.warnings)


def test_recovered_rows_are_reported_not_hidden():
    score = score_ptr_parse(
        _quality(rows_recovered=2, rows_detected=2, rows_parsed=2), [_txn()] * 2
    )
    assert any("recovered" in w for w in score.warnings)
    # Recovery is reported, not penalised: the row was read correctly, just by
    # the weaker path, and the warning is what a person acts on.
    assert score.confidence == 1.0


# --------------------------------------------------------------------------
# Annual filings
# --------------------------------------------------------------------------


def test_an_annual_filing_with_holdings_scores_one():
    assert score_fd_parse(True, assets=12, liabilities=2).confidence == 1.0


def test_an_annual_filing_with_nothing_in_it_scores_zero():
    score = score_fd_parse(True, assets=0, liabilities=0)
    assert score.confidence == 0.0
    assert "no assets or liabilities" in score.summary


def test_an_annual_filing_that_errored_scores_zero():
    assert score_fd_parse(True, assets=5, liabilities=0, errors=["boom"]).confidence == 0.0


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_real_filing_scores_well(path):
    """All six parse cleanly now, and the score has to agree.

    Before the collapsed-row recovery two of these produced nothing at all
    while being recorded as parsed successfully.
    """
    filing = json.loads(path.read_text())
    parser = PTRParser()
    quality = ParseQuality(text_extracted=True, tables_found=True)
    transactions = parser._parse_tables(filing["tables"], quality)

    score = score_ptr_parse(quality.as_dict(), transactions)

    assert transactions, f"{filing['document_id']} parsed to nothing"
    assert score.confidence > 0.9, f"{filing['document_id']} scored {score.confidence}"


def test_the_corpus_holds_more_than_twice_what_it_used_to():
    # 16 transactions before the collapsed-row recovery, 34 after. Pinned so a
    # regression in the recovery shows up as a number rather than as silence.
    parser = PTRParser()
    total = sum(
        len(parser._parse_tables(json.loads(path.read_text())["tables"])) for path in FIXTURES
    )
    assert total == 34
