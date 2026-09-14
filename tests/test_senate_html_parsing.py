"""Senate filings are HTML. Handing them to a PDF parser produced this:

    parsed=True  confidence=0.0  transactions=0
    parse_error: "no text layer in PDF - likely a scan"

on every single one of the 458 Senate filings that ingestion had just succeeded
in storing. They are not scans. Senate eFD serves HTML; the House Clerk serves
PDFs; pdfplumber found no text layer and reported the only explanation it has.

Two costs, not one. No Senate trade reached the database, so every finding on
the site stayed `chamber: house`. And the landing page counted those filings as
"scans of paper forms, which hold no machine-readable text" — a false statement
about documents that are machine-readable in the most literal sense, on the card
whose whole job is to be honest about coverage.

The fixture is a real eFD periodic transaction report captured from the live
service.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.parsing.senate_html_parser import SenateHtmlParser

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "senate_ptr_filing.html"


@pytest.fixture(scope="module")
def parsed():
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    result = SenateHtmlParser().parse_senate_html(str(FIXTURE))
    assert result["transactions"], "the fixture parsed to nothing — tests below would be vacuous"
    return result


class TestARealSenateFilingIsRead:
    def test_every_row_becomes_a_transaction(self, parsed):
        quality = parsed["quality"]

        assert quality["rows_parsed"] == quality["rows_detected"], (
            f"{quality['rows_detected'] - quality['rows_parsed']} rows were seen and dropped"
        )
        assert quality["rows_parsed"] == 10

    def test_it_reports_no_errors(self, parsed):
        assert parsed["parse_errors"] == []

    def test_text_and_tables_are_both_found(self, parsed):
        assert parsed["quality"]["text_extracted"] is True
        assert parsed["quality"]["tables_found"] is True

    def test_dates_directions_and_bands_survive(self, parsed):
        for transaction in parsed["transactions"]:
            assert isinstance(transaction["transaction_date"], datetime)
            assert transaction["transaction_type"] in {"purchase", "sale", "exchange"}
            assert transaction["description"]

    def test_amounts_stay_bands(self, parsed):
        """The oldest rule in this project: a filing reports a range, never a
        figure, so both ends are kept."""
        banded = [
            t
            for t in parsed["transactions"]
            if t["amount_min"] is not None and t["amount_max"] is not None
        ]
        assert banded, "no transaction carried a band"
        for transaction in banded:
            assert isinstance(transaction["amount_min"], Decimal)
            assert transaction["amount_max"] >= transaction["amount_min"]

    def test_a_ticker_is_read_when_efd_gives_one(self, parsed):
        tickers = {t["ticker"] for t in parsed["transactions"] if t["ticker"]}
        assert tickers, "no tickers at all — the column is being missed"

    def test_a_bond_with_no_ticker_is_still_a_transaction(self, parsed):
        """eFD leaves the ticker column blank for bonds and municipal
        securities. Those are still disclosed trades and must not be dropped."""
        untickered = [t for t in parsed["transactions"] if not t["ticker"]]
        assert untickered, "fixture no longer contains an untickered holding"
        for transaction in untickered:
            assert transaction["description"]
            assert transaction["amount_min"] is not None


class TestColumnsAreReadByNameNotPosition:
    """eFD owns its column order. Reading by index is how a reordered table
    silently produces garbage rather than an error."""

    def test_a_reordered_table_still_parses(self, tmp_path):
        html = """
        <table>
          <tr><th>Amount</th><th>Type</th><th>Asset Name</th>
              <th>Ticker</th><th>Transaction Date</th><th>Owner</th></tr>
          <tr><td>$1,001 - $15,000</td><td>Purchase</td><td>Apple Inc</td>
              <td>AAPL</td><td>03/14/2025</td><td>Self</td></tr>
        </table>
        """
        path = tmp_path / "reordered.html"
        path.write_text(html)

        result = SenateHtmlParser().parse_senate_html(str(path))

        assert len(result["transactions"]) == 1
        transaction = result["transactions"][0]
        assert transaction["ticker"] == "AAPL"
        assert transaction["transaction_type"] == "purchase"
        assert transaction["amount_min"] == Decimal("1001")
        assert transaction["transaction_date"] == datetime(2025, 3, 14)


class TestAGenuineScanIsDistinguishable:
    """A paper filing really is a scan — eFD serves those as an image with no
    table. Saying that distinctly keeps "we cannot read this" separate from
    "this document is empty", which is the distinction the whole coverage card
    rests on."""

    def test_a_filing_with_no_table_says_so(self, tmp_path):
        path = tmp_path / "scan.html"
        path.write_text("<html><body><img src='/scan.png'><p>Paper filing</p></body></html>")

        result = SenateHtmlParser().parse_senate_html(str(path))

        assert result["transactions"] == []
        assert result["parse_errors"], "a filing that could not be read reported no error"
        assert "no transaction table" in result["parse_errors"][0]

    def test_a_missing_file_is_an_error_not_a_crash(self, tmp_path):
        result = SenateHtmlParser().parse_senate_html(str(tmp_path / "nope.html"))

        assert result["transactions"] == []
        assert result["parse_errors"]


class TestTheOrchestratorRoutesHtmlToThisParser:
    """The wiring. Without it the parser exists and nothing calls it."""

    def _senate_filing(self, db, tmp_path):
        from src.db.models import Chamber, Disclosure, Member, Party

        member = Member(
            bioguide_id="SH00001",
            first_name="Sen",
            last_name="Html",
            chamber=Chamber.SENATE,
            party=Party.DEMOCRAT,
            state="PA",
        )
        db.add(member)
        db.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2025,
            filing_type="PTR",
            filing_date=datetime(2025, 12, 30),
            document_id="SHTML-1",
            document_url="https://efdsearch.senate.gov/search/view/ptr/abc/",
            is_ptr=True,
            parsed=False,
        )
        db.add(disclosure)
        db.commit()
        db.refresh(disclosure)
        return disclosure

    def test_an_html_filing_yields_transactions(self, db_session, tmp_path):
        from src.db.models import Transaction
        from src.ingestion.orchestrator import IngestionOrchestrator

        saved = tmp_path / "SHTML-1.html"
        saved.write_text(FIXTURE.read_text())

        disclosure = self._senate_filing(db_session, tmp_path)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]

        assert orch.parse_disclosure(db_session, disclosure) is True

        stored = (
            db_session.query(Transaction).filter(Transaction.disclosure_id == disclosure.id).count()
        )
        assert stored == 10, (
            f"{stored} transactions stored from a Senate filing holding 10 — the "
            "HTML is going to the PDF parser again"
        )

    def test_it_is_not_recorded_as_an_unreadable_scan(self, db_session, tmp_path):
        from src.ingestion.orchestrator import IngestionOrchestrator

        saved = tmp_path / "SHTML-1.html"
        saved.write_text(FIXTURE.read_text())

        disclosure = self._senate_filing(db_session, tmp_path)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]
        orch.parse_disclosure(db_session, disclosure)
        db_session.refresh(disclosure)

        assert disclosure.has_text_layer is True, (
            "a machine-readable HTML filing was recorded as having no text layer, "
            "which the landing page reports to the public as a paper scan"
        )
        assert (disclosure.parse_confidence or 0) > 0
