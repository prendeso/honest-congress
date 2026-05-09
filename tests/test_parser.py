"""Tests for PDF parser."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.ingestion.date_utils import choose_filing_date, choose_transaction_date, coerce_non_future
from src.parsing.pdf_parser import DisclosureParser


class TestDisclosureParser:
    """Tests for DisclosureParser."""

    @pytest.fixture
    def parser(self):
        return DisclosureParser()

    def test_extract_ticker_explicit(self, parser):
        """Test extracting ticker from explicit notation."""
        assert parser._extract_ticker("Apple Inc (AAPL)") == "AAPL"
        assert parser._extract_ticker("Microsoft [MSFT] stock") == "MSFT"

    def test_extract_ticker_from_stock_text(self, parser):
        """Test extracting ticker from stock description."""
        result = parser._extract_ticker("NVDA common stock purchased")
        # Should find NVDA as it's near "stock" keyword
        assert result == "NVDA"

    def test_extract_ticker_none(self, parser):
        """Test no ticker extracted from non-stock text."""
        result = parser._extract_ticker("Real estate property in Florida")
        assert result is None

    def test_determine_asset_type_stock(self, parser):
        """Test asset type determination for stocks."""
        assert parser._determine_asset_type("Apple Inc common stock") == "stock"
        assert parser._determine_asset_type("100 shares of Microsoft") == "stock"

    def test_determine_asset_type_bond(self, parser):
        """Test asset type determination for bonds."""
        assert parser._determine_asset_type("US Treasury Bond") == "bond"
        assert parser._determine_asset_type("Corporate notes") == "bond"

    def test_determine_asset_type_mutual_fund(self, parser):
        """Test asset type determination for mutual funds."""
        assert parser._determine_asset_type("Vanguard S&P 500 Index Fund") == "mutual_fund"
        assert parser._determine_asset_type("Fidelity Growth ETF") == "mutual_fund"

    def test_determine_asset_type_real_estate(self, parser):
        """Test asset type determination for real estate."""
        assert parser._determine_asset_type("Real estate in California") == "real_estate"
        assert parser._determine_asset_type("Rental property") == "real_estate"

    def test_determine_asset_type_retirement(self, parser):
        """Test asset type determination for retirement accounts."""
        assert parser._determine_asset_type("401k account") == "retirement"
        assert parser._determine_asset_type("Traditional IRA") == "retirement"

    def test_determine_asset_type_bank(self, parser):
        """Test asset type determination for bank accounts."""
        assert parser._determine_asset_type("Chase Bank savings account") == "bank_account"
        assert parser._determine_asset_type("Checking account at Wells Fargo") == "bank_account"

    def test_parse_value_range_known(self, parser):
        """Test parsing known value ranges."""
        min_val, max_val = parser._parse_value_range("$1,001 - $15,000")
        assert min_val == Decimal("1001")
        assert max_val == Decimal("15000")

    def test_parse_value_range_custom(self, parser):
        """Test parsing custom value ranges."""
        min_val, max_val = parser._parse_value_range("$50,000 - $75,000")
        assert min_val == Decimal("50000")
        assert max_val == Decimal("75000")

    def test_parse_value_range_single(self, parser):
        """Test parsing single value."""
        min_val, max_val = parser._parse_value_range("$100,000")
        assert min_val == Decimal("100000")
        assert max_val == Decimal("100000")

    def test_parse_value_range_empty(self, parser):
        """Test parsing empty string."""
        min_val, max_val = parser._parse_value_range("")
        assert min_val is None
        assert max_val is None

    def test_normalize_transaction_type(self, parser):
        """Test transaction type normalization."""
        assert parser._normalize_transaction_type("Purchase") == "purchase"
        assert parser._normalize_transaction_type("Buy") == "purchase"
        assert parser._normalize_transaction_type("Sale") == "sale"
        assert parser._normalize_transaction_type("Sold") == "sale"
        assert parser._normalize_transaction_type("Exchange") == "exchange"
        assert parser._normalize_transaction_type("Unknown") is None

    def test_parse_date_formats(self, parser):
        """Test date parsing for various formats."""
        assert parser._parse_date("01/15/2024") == datetime(2024, 1, 15)
        assert parser._parse_date("2024-01-15") == datetime(2024, 1, 15)
        assert parser._parse_date("January 15, 2024") == datetime(2024, 1, 15)
        assert parser._parse_date("") is None
        assert parser._parse_date("invalid") is None

    def test_looks_like_value_range(self, parser):
        """Test value range detection."""
        assert parser._looks_like_value_range("$1,001 - $15,000") is True
        assert parser._looks_like_value_range("$50,000") is True
        assert parser._looks_like_value_range("None") is False
        assert parser._looks_like_value_range("") is False


class TestDateUtils:
    def test_coerce_non_future(self):
        now = datetime(2026, 2, 1, 12, 0, 0)
        past = now - timedelta(days=1)
        future = now + timedelta(days=1)

        assert coerce_non_future(past, now) == past
        assert coerce_non_future(future, now) is None
        assert coerce_non_future(None, now) is None

    def test_choose_filing_date(self):
        now = datetime(2026, 2, 1, 12, 0, 0)
        filing_year = 2024
        future = now + timedelta(days=10)
        past = now - timedelta(days=10)

        assert choose_filing_date(past, filing_year, now) == past
        assert choose_filing_date(future, filing_year, now) == now
        assert choose_filing_date(None, filing_year, now) == datetime(2024, 12, 31)

    def test_choose_transaction_date(self):
        now = datetime(2026, 2, 1, 12, 0, 0)
        past = now - timedelta(days=2)
        future = now + timedelta(days=2)

        assert choose_transaction_date(past, None, now) == past
        assert choose_transaction_date(future, past, now) == past
        assert choose_transaction_date(None, None, now) == now
