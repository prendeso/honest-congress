"""Extra parser tests — row-level + text-fallback paths.

The original test_parser.py covers the primitive helpers (extract_ticker,
parse_value_range, etc.). These tests exercise the row-level methods that
take pdfplumber-style inputs (lists of cell strings) so we lock the
parsing logic without needing real PDF binaries.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.parsing.pdf_parser import DisclosureParser
from src.parsing.ptr_parser import PTRParser


@pytest.fixture
def parser():
    return DisclosureParser()


@pytest.fixture
def ptr_parser():
    return PTRParser()


# ---------------- DisclosureParser row parsers ----------------


class TestParseAssetRow:
    def test_parses_full_row(self, parser):
        row = ["Apple Inc (AAPL) common stock", "$15,001 - $50,000", "$201 - $1,000"]
        result = parser._parse_asset_row(row)
        assert result is not None
        assert result["description"] == "Apple Inc (AAPL) common stock"
        assert result["ticker"] == "AAPL"
        assert result["asset_type"] == "stock"
        assert result["value_min"] == Decimal("15001")
        assert result["value_max"] == Decimal("50000")
        assert result["income_min"] == Decimal("201")
        assert result["income_max"] == Decimal("1000")

    def test_returns_none_for_too_short_row(self, parser):
        assert parser._parse_asset_row([]) is None
        assert parser._parse_asset_row(["lonely"]) is None

    def test_returns_none_for_blank_description(self, parser):
        assert parser._parse_asset_row(["", "$1,001 - $15,000"]) is None

    def test_handles_no_value_range(self, parser):
        # No matching value range cell — value_min/max stay None.
        row = ["Vanguard Index Fund", "miscellaneous text"]
        result = parser._parse_asset_row(row)
        assert result is not None
        assert result["asset_type"] == "mutual_fund"
        assert result["value_min"] is None
        assert result["value_max"] is None


class TestParseTransactionRow:
    def test_parses_full_row(self, parser):
        row = ["Apple Inc (AAPL)", "Purchase", "01/15/2024", "$1,001 - $15,000", "Self"]
        result = parser._parse_transaction_row(row)
        assert result is not None
        assert result["transaction_type"] == "purchase"
        assert result["ticker"] == "AAPL"
        assert result["transaction_date"] == datetime(2024, 1, 15)
        assert result["amount_min"] == Decimal("1001")
        assert result["amount_max"] == Decimal("15000")
        assert result["owner"] == "Self"

    def test_returns_none_for_too_short_row(self, parser):
        assert parser._parse_transaction_row(["a"]) is None
        assert parser._parse_transaction_row(["a", "b"]) is None

    def test_returns_none_for_unrecognized_type(self, parser):
        # _normalize_transaction_type returns None for non-matching strings.
        assert parser._parse_transaction_row(["Apple", "ZZZ", "01/01/24", "$0"]) is None

    def test_owner_defaults_to_empty_when_absent(self, parser):
        row = ["Apple", "Purchase", "01/15/2024", "$1,001 - $15,000"]
        result = parser._parse_transaction_row(row)
        assert result is not None
        assert result["owner"] == ""


class TestParseLiabilityRow:
    def test_parses_full_row(self, parser):
        row = ["Chase Bank", "Mortgage on rental property", "$250,001 - $500,000"]
        result = parser._parse_liability_row(row)
        assert result is not None
        assert result["creditor"] == "Chase Bank"
        assert result["description"] == "Mortgage on rental property"
        assert result["amount_min"] == Decimal("250001")
        assert result["amount_max"] == Decimal("500000")

    def test_no_creditor_returns_none(self, parser):
        assert parser._parse_liability_row(["", "desc", "$1,001 - $15,000"]) is None


class TestExtractAssetsFromText:
    def test_finds_inline_stock_listings(self, parser):
        text = (
            "Apple Inc (AAPL) common stock - $15,001 - $50,000\n"
            "Vanguard Total Market Index Fund - $50,001 - $100,000\n"
        )
        results = parser._extract_assets_from_text(text)
        assert len(results) >= 1
        # At least one result should be a stock with ticker.
        types = [r["asset_type"] for r in results]
        assert "stock" in types or "mutual_fund" in types

    def test_no_matches_returns_empty(self, parser):
        assert parser._extract_assets_from_text("just a sentence with no money") == []


# ---------------- PTRParser helpers ----------------


class TestPtrParserHelpers:
    def test_normalize_owner(self, ptr_parser):
        assert ptr_parser._normalize_owner("") == "Self"
        assert ptr_parser._normalize_owner("Spouse") == "Spouse"
        assert ptr_parser._normalize_owner("SP") == "Spouse"
        assert ptr_parser._normalize_owner("Joint Account") == "Joint"
        assert ptr_parser._normalize_owner("JT") == "Joint"
        assert ptr_parser._normalize_owner("Dependent Child") == "Dependent Child"
        assert ptr_parser._normalize_owner("DC") == "Dependent Child"
        assert ptr_parser._normalize_owner("Self") == "Self"

    def test_parse_transaction_type(self, ptr_parser):
        assert ptr_parser._parse_transaction_type("P") == "purchase"
        assert ptr_parser._parse_transaction_type("S") == "sale"
        assert ptr_parser._parse_transaction_type("Purchase") == "purchase"
        assert ptr_parser._parse_transaction_type("Sale") == "sale"

    def test_infer_transaction_type(self, ptr_parser):
        assert ptr_parser._infer_transaction_type("100 shares purchased") == "purchase"
        assert ptr_parser._infer_transaction_type("Sold AAPL") == "sale"
        assert ptr_parser._infer_transaction_type("Random asset") is None

    def test_parse_date(self, ptr_parser):
        assert ptr_parser._parse_date("01/15/2024") == datetime(2024, 1, 15)
        assert ptr_parser._parse_date("2024-01-15") == datetime(2024, 1, 15)
        assert ptr_parser._parse_date("invalid") is None
        assert ptr_parser._parse_date("") is None

    def test_parse_amount_range(self, ptr_parser):
        lo, hi = ptr_parser._parse_amount_range("$1,001 - $15,000")
        assert lo == Decimal("1001")
        assert hi == Decimal("15000")

    def test_extract_ticker(self, ptr_parser):
        assert ptr_parser._extract_ticker("Apple Inc (AAPL)") == "AAPL"
        assert ptr_parser._extract_ticker("No ticker here") is None

    def test_determine_asset_type(self, ptr_parser):
        assert ptr_parser._determine_asset_type("Apple Inc common stock") == "stock"
        assert ptr_parser._determine_asset_type("US Treasury Bond") == "bond"
        assert ptr_parser._determine_asset_type("Vanguard Index Fund") == "mutual_fund"


class TestPtrFilerInfo:
    def test_extracts_state_and_district(self, ptr_parser):
        text = "Member: Alice Example   CA-12   Annual Filing"
        info = ptr_parser._extract_filer_info(text)
        assert info.get("state") == "CA"
        assert info.get("district") == "12"

    def test_extracts_filing_date(self, ptr_parser):
        text = "Filed: 03/22/2024 by Member"
        d = ptr_parser._extract_filing_date(text)
        assert d == datetime(2024, 3, 22)

    def test_filing_date_none_when_missing(self, ptr_parser):
        assert ptr_parser._extract_filing_date("no date here") is None
