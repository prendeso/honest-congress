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


# What eFD actually serves for a paper filing: its own chrome, page navigation,
# and one GIF per page from the media host. Reproduced from a live
# `/search/view/paper/` response rather than invented.
SCAN_MARKUP = """
<html><body>
  <a>Skip to main content</a>
  <h1>Filing Document - Print View</h1>
  <img src="/static/images/logo.svg">
  <p>1 2 3 4 Page 1 of 4 Rotate</p>
  <img src="https://efd-media-public.senate.gov/media/2026/2/000/000/000000513.gif">
  <img src="https://efd-media-public.senate.gov/media/2026/2/000/000/000000514.gif">
</body></html>
"""


class TestAGenuineScanIsDistinguishable:
    """A paper filing really is a scan — eFD serves those as page images with no
    table. Saying that distinctly keeps "we cannot read this" separate from
    "this document is empty", which is the distinction the whole coverage card
    rests on.

    The distinction was being lost at the last step. `parse_quality_summary`
    separates scans from the parser's own failures on `has_text_layer`, which
    comes from `quality["text_extracted"]` — and on an HTML page that was
    `bool(soup.get_text())`, true of eFD's navigation chrome whatever the
    filing contains. So every Senate scan was counted as a filing the parser
    failed to read. Measured against the live service: 5 of 43 sampled PTRs are
    paper, and every one of them landed in that bucket.
    """

    def test_a_scan_is_not_counted_as_a_text_layer(self, tmp_path):
        path = tmp_path / "scan.html"
        path.write_text(SCAN_MARKUP)

        result = SenateHtmlParser().parse_senate_html(str(path))

        assert result["transactions"] == []
        assert result["quality"]["text_extracted"] is False, (
            "a scan with a text layer is counted as the parser's own failure "
            "rather than as a document with nothing to read"
        )

    def test_a_scan_and_an_unknown_layout_do_not_share_one_message(self, tmp_path):
        """The old message named both — "a scanned paper filing, or a layout
        this parser does not know" — so it committed to neither, and a reader
        could not tell which had happened from the stored `parse_error`."""
        scan = tmp_path / "scan.html"
        scan.write_text(SCAN_MARKUP)
        odd = tmp_path / "odd.html"
        odd.write_text("<html><body><p>Filing</p></body></html>")

        parser = SenateHtmlParser()
        scan_error = parser.parse_senate_html(str(scan))["parse_errors"][0]
        odd_error = parser.parse_senate_html(str(odd))["parse_errors"][0]

        assert scan_error != odd_error
        assert "scanned paper filing" in scan_error
        assert "scanned paper filing" not in odd_error

    def test_an_unknown_layout_is_still_the_parser_s_failure(self, tmp_path):
        """No table and no page images. That is not a scan, and blaming one
        would hide a layout change behind a category that excuses it."""
        path = tmp_path / "odd.html"
        path.write_text(
            "<html><body><img src='/static/images/logo.svg'><p>Filing</p></body></html>"
        )

        result = SenateHtmlParser().parse_senate_html(str(path))

        assert result["transactions"] == []
        assert result["quality"]["text_extracted"] is True
        assert "not a scan" in result["parse_errors"][0]

    def test_a_readable_filing_is_never_mistaken_for_a_scan(self, parsed):
        assert parsed["quality"]["text_extracted"] is True

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


class TestStoredSenateFilingsCanBeReReadAtAll:
    """The 458 already-stored Senate filings were unreachable by both modes.

    Each carries `parsed=True, has_text_layer=False, confidence=0.0` — the
    verdict pdfplumber reached when it was handed HTML. That combination is a
    trap:

      * `fresh` selects `parsed == False`, so it skips them.
      * `--min-confidence` excludes `has_text_layer == False` as scans, so it
        skips them too.

    A correct HTML parser plus filings it can never be pointed at is a fix that
    delivers nothing, so these pin the escape route.
    """

    def _filing(self, db, url, *, parsed, text_layer, confidence, doc_id):
        from src.db.models import Chamber, Disclosure, Member, Party

        member = db.query(Member).filter(Member.bioguide_id == "RR00001").first()
        if member is None:
            member = Member(
                bioguide_id="RR00001",
                first_name="Re",
                last_name="Read",
                chamber=Chamber.SENATE,
                party=Party.DEMOCRAT,
                state="VA",
            )
            db.add(member)
            db.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2025,
            filing_type="PTR",
            filing_date=datetime(2025, 6, 1),
            document_id=doc_id,
            document_url=url,
            is_ptr=True,
            parsed=parsed,
            has_text_layer=text_layer,
            parse_confidence=confidence,
        )
        db.add(disclosure)
        db.commit()
        return disclosure

    def _selected(self, db, tmp_path):
        """The filings a `--min-confidence 1.0` run would pick up."""
        from src.ingestion.orchestrator import IngestionOrchestrator

        picked = []
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.parse_disclosure = lambda db_, d, pdf_path=None: (  # type: ignore[method-assign]
            picked.append(d.document_id) or True
        )
        orch.parse_disclosures(db, min_confidence=1.0, delay=0)
        return picked

    def test_a_senate_filing_misread_as_a_scan_is_reachable(self, db_session, tmp_path):
        self._filing(
            db_session,
            "https://efdsearch.senate.gov/search/view/ptr/abc/",
            parsed=True,
            text_layer=False,
            confidence=0.0,
            doc_id="SEN-TRAPPED",
        )

        assert "SEN-TRAPPED" in self._selected(db_session, tmp_path), (
            "the HTML parser can never be pointed at the filings it was written for"
        )

    def test_a_house_scan_is_still_skipped(self, db_session, tmp_path):
        """The exemption must not undo what the filter is for: a real PDF scan
        scores 0.0 every time, and re-reading 12.7% of the House corpus to learn
        that again is the cost this filter exists to avoid."""
        self._filing(
            db_session,
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/123.pdf",
            parsed=True,
            text_layer=False,
            confidence=0.0,
            doc_id="HOUSE-SCAN",
        )

        assert "HOUSE-SCAN" not in self._selected(db_session, tmp_path)

    def test_a_readable_house_filing_below_the_bar_is_still_reached(self, db_session, tmp_path):
        self._filing(
            db_session,
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/456.pdf",
            parsed=True,
            text_layer=True,
            confidence=0.4,
            doc_id="HOUSE-POOR",
        )

        assert "HOUSE-POOR" in self._selected(db_session, tmp_path)


class TestTheParserSaysThatItReadTheHeaders:
    """`score_ptr_parse` assumes columns were guessed unless told otherwise.

    Reading columns by header name is the whole design of this parser --
    `_column_index` maps every field from the table's own `<th>` text, and
    `TestColumnsAreReadByNameNotPosition` reorders the table to prove it. But it
    never set `quality.headers_recognised`, which only the PDF path did, so
    every Senate filing was scored as though the positions had been assumed.

    Measured on the live site: all eighteen readable Senate filings in a sample
    carried the warning "column positions assumed, not read from a header row"
    -- the exact reverse of what happened -- at confidence 0.5.

    The confidence is the half that actually bites. `NO_HEADER_CEILING` is 0.5,
    so every Senate filing permanently matched `parse --min-confidence 1.0`:
    every future re-parse would re-download and re-read the whole Senate corpus
    to arrive at 0.5 again, and the queue could never converge. Against real
    filings the same six now score 1.0, 0.0, 1.0, 1.0, 0.0, 1.0 -- the two
    zeroes being `/view/paper/` image scans, which is correct.
    """

    def test_headers_are_recorded_as_recognised(self, parsed):
        assert parsed["quality"]["headers_recognised"] is True

    def test_the_filing_is_not_warned_about_assumed_columns(self, parsed):
        from src.parsing.confidence import score_ptr_parse

        score = score_ptr_parse(parsed["quality"], parsed["transactions"])

        assert not any("column positions assumed" in w for w in score.warnings), score.warnings

    def test_a_clean_read_can_reach_full_confidence(self, parsed):
        from src.parsing.confidence import NO_HEADER_CEILING, score_ptr_parse

        score = score_ptr_parse(parsed["quality"], parsed["transactions"])

        # The point is not the exact number; it is that the score is no longer
        # held under the ceiling that kept every Senate filing in the re-parse
        # queue for ever.
        assert score.confidence > NO_HEADER_CEILING, score.warnings

    def test_a_table_whose_headers_do_not_yield_the_required_columns_is_still_honest(self):
        from bs4 import BeautifulSoup

        from src.parsing.ptr_parser import ParseQuality

        # No Amount column, so the parse cannot proceed -- and must not claim it
        # read headers it could not use.
        soup = BeautifulSoup(
            "<table><tr><th>Something</th><th>Else</th></tr><tr><td>a</td><td>b</td></tr></table>",
            "html.parser",
        )
        quality = ParseQuality()
        rows = SenateHtmlParser()._rows_to_transactions(soup.find("table"), quality)

        assert rows == []
        assert quality.headers_recognised is False
