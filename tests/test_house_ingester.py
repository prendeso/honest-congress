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


# Mirrors the real House Clerk index, verified against the live 2024 file: a
# single {year}FD.xml carrying every filing type, with Periodic Transaction
# Reports marked FilingType "P". There is no separate {year}PTR.xml -- that URL
# 404s, which is what used to make PTR ingestion silently yield nothing.
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
  <Member>
    <Prefix></Prefix>
    <First>Charlie</First>
    <Last>Trader</Last>
    <Suffix></Suffix>
    <FilingType>P</FilingType>
    <StateDst>TX02</StateDst>
    <FilingDate>03/22/2024</FilingDate>
    <DocID>20019999</DocID>
  </Member>
  <Member>
    <Prefix></Prefix>
    <First>Dana</First>
    <Last>Annual</Last>
    <Suffix></Suffix>
    <FilingType>O</FilingType>
    <StateDst>OH07</StateDst>
    <FilingDate>05/15/2024</FilingDate>
    <DocID>20012347</DocID>
  </Member>
</FinancialDisclosure>
"""


@pytest.fixture
def ingester():
    return HouseIngester()


class TestParseXmlIndex:
    def test_skips_ptr_filings(self, ingester):
        """PTRs belong to the PTR path, which builds the ptr-pdfs URL.

        When both paths claimed the same document_id, whichever inserted first
        won -- storing PTRs as annual filings pointing at a financial-pdfs URL
        that 404s for them.
        """
        results = ingester._parse_xml_index(_FD_XML, year=2024)

        assert len(results) == 2, "the two FilingType P records must be excluded"
        assert {r["last_name"] for r in results} == {"Test", "Annual"}

    def test_state_and_district_split(self, ingester):
        bob = ingester._parse_xml_index(_FD_XML, year=2024)[0]
        assert bob["state"] == "NY"
        assert bob["district"] == "03"
        assert bob["chamber"] == "house"
        # FD records don't carry an is_ptr key (only PTR parsing sets it).
        assert "is_ptr" not in bob

    def test_full_name_builds_from_parts(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        assert results[0]["full_name"] == "Bob Test Jr."

    def test_filing_date_parsed(self, ingester):
        d = ingester._parse_xml_index(_FD_XML, year=2024)[0]["filing_date"]
        assert d.year == 2024 and d.month == 5 and d.day == 15

    def test_pdf_url_built_from_year_and_docid(self, ingester):
        results = ingester._parse_xml_index(_FD_XML, year=2024)
        assert results[0]["document_url"].endswith("/2024/20012346.pdf")
        assert "financial-pdfs" in results[0]["document_url"]

    def test_invalid_xml_returns_empty(self, ingester):
        assert ingester._parse_xml_index(b"<not xml", year=2024) == []


class TestParsePtrXmlIndex:
    def test_selects_only_ptr_filings(self, ingester):
        results = ingester._parse_ptr_xml_index(_FD_XML, year=2024)

        assert len(results) == 2
        assert {r["last_name"] for r in results} == {"Example", "Trader"}
        assert all(r["is_ptr"] is True for r in results)
        assert all(r["filing_type"] == "PTR" for r in results)

    def test_pdf_url_uses_ptr_path(self, ingester):
        results = ingester._parse_ptr_xml_index(_FD_XML, year=2024)
        urls = {r["document_url"] for r in results}

        assert all("ptr-pdfs" in u for u in urls)
        assert any(u.endswith("/2024/20019999.pdf") for u in urls)

    def test_the_two_paths_do_not_overlap(self, ingester):
        annual = {r["document_id"] for r in ingester._parse_xml_index(_FD_XML, year=2024)}
        ptr = {r["document_id"] for r in ingester._parse_ptr_xml_index(_FD_XML, year=2024)}

        assert not (annual & ptr)
        assert len(annual | ptr) == 4, "every filing in the index is claimed exactly once"


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
        assert results[0]["last_name"] == "Test"


class TestFetchPtrXmlIndex:
    def test_reads_the_index_that_exists(self, ingester):
        """Must fetch {year}FD.xml, not {year}PTR.xml.

        The PTR-specific URL returns 404. fetch_ptr_xml_index caught the error
        and returned [], so PTR ingestion reported zero rather than failing --
        and PTRs are the only source of trades.
        """
        with patch.object(
            ingester.session, "get", return_value=_mock_response(_FD_XML)
        ) as mock_get:
            results = ingester.fetch_ptr_xml_index(2024)

        url = mock_get.call_args.args[0]
        assert "/2024FD.xml" in url
        assert "PTR.xml" not in url
        assert len(results) == 2
