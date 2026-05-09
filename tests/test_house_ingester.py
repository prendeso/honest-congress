"""HouseIngester tests with synthetic XML fixtures + mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion.house import HouseIngester


def _mock_response(content: bytes, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    if status_code >= 400:
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=status_code))
        response.raise_for_status.side_effect = err
    else:
        response.raise_for_status.return_value = None
    return response


# Synthetic XML matching the House Clerk index format.
_FD_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FinancialDisclosure>
  <Member>
    <Prefix>Hon.</Prefix>
    <First>Alice</First>
    <Last>Example</Last>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>CA12</StateDst>
    <FilingDate>05/15/2024</FilingDate>
    <DocID>20012345</DocID>
  </Member>
  <Member>
    <Prefix></Prefix>
    <First>Bob</First>
    <Last>Test</Last>
    <Suffix>Jr.</Suffix>
    <FilingType>A</FilingType>
    <StateDst>NY03</StateDst>
    <FilingDate>05/15/2024</FilingDate>
    <DocID>20012346</DocID>
  </Member>
</FinancialDisclosure>
"""


_PTR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FinancialDisclosure>
  <Member>
    <First>Charlie</First>
    <Last>Trader</Last>
    <FilingType></FilingType>
    <StateDst>TX02</StateDst>
    <FilingDate>03/22/2024</FilingDate>
    <DocID>20019999</DocID>
  </Member>
</FinancialDisclosure>
"""


@pytest.fixture
def ingester():
    return HouseIngester()


class TestParseXmlIndex:
    def test_parses_two_members(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        assert len(results) == 2

    def test_state_and_district_split(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        alice = results[0]
        assert alice["state"] == "CA"
        assert alice["district"] == "12"
        assert alice["chamber"] == "house"
        # FD records don't carry an is_ptr key (only PTR parsing sets it).
        assert "is_ptr" not in alice

    def test_full_name_builds_from_parts(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        assert results[0]["full_name"] == "Hon. Alice Example"
        assert results[1]["full_name"] == "Bob Test Jr."

    def test_filing_date_parsed(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        d = results[0]["filing_date"]
        assert d.year == 2024 and d.month == 5 and d.day == 15

    def test_pdf_url_built_from_year_and_docid(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        assert results[0]["document_url"].endswith("/2024/20012345.pdf")
        assert "financial-pdfs" in results[0]["document_url"]

    def test_invalid_xml_returns_empty(self, ingester):
        assert ingester._parse_xml_index(b"<not xml", year=2024) == []


class TestParsePtrXmlIndex:
    def test_marks_as_ptr(self, ingester):
        results = ingester._parse_ptr_xml_index(_PTR_XML, year=2024)
        assert len(results) == 1
        assert results[0]["is_ptr"] is True
        assert results[0]["filing_type"] == "PTR"

    def test_pdf_url_uses_ptr_path(self, ingester):
        results = ingester._parse_ptr_xml_index(_PTR_XML, year=2024)
        assert "ptr-pdfs" in results[0]["document_url"]
        assert results[0]["document_url"].endswith("/2024/20019999.pdf")


class TestFetchAnnualXmlIndex:
    def test_calls_correct_url(self, ingester):
        with patch.object(
            ingester.session, "get", return_value=_mock_response(_FD_XML)
        ) as mock_get:
            ingester.fetch_annual_xml_index(2024)
        url = mock_get.call_args.args[0]
        assert "/2024FD.xml" in url

    def test_http_error_returns_empty(self, ingester):
        with patch.object(ingester.session, "get", side_effect=requests.RequestException()):
            assert ingester.fetch_annual_xml_index(2024) == []

    def test_returns_parsed_records(self, ingester):
        with patch.object(ingester.session, "get", return_value=_mock_response(_FD_XML)):
            results = ingester.fetch_annual_xml_index(2024)
        assert len(results) == 2
        assert results[0]["last_name"] == "Example"


class TestFetchPtrXmlIndex:
    def test_calls_correct_url(self, ingester):
        with patch.object(
            ingester.session, "get", return_value=_mock_response(_PTR_XML)
        ) as mock_get:
            ingester.fetch_ptr_xml_index(2024)
        url = mock_get.call_args.args[0]
        assert "/2024PTR.xml" in url
