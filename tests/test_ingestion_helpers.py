"""Tests for src/ingestion/_helpers.py.

These cover the standalone helpers extracted from `IngestionOrchestrator`.
The orchestrator class still wraps them for backward compatibility
(see `tests/test_orchestrator.py`); these tests pin the function-level
contract directly so a future decomposition can rip the class out
without losing coverage.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from src.db.models import Chamber, Disclosure, Member, Party
from src.ingestion._helpers import (
    build_alt_pdf_url,
    fix_future_dates,
    record_failed_download,
)


def _make_member(db) -> Member:
    m = Member(
        bioguide_id="H000001",
        first_name="Help",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# ---------------- build_alt_pdf_url ----------------


class TestBuildAltPdfUrl:
    def test_returns_none_when_url_blank(self):
        d = Disclosure(document_url=None, document_id="X", filing_year=2024, is_ptr=False)
        assert build_alt_pdf_url(d) is None

    def test_strips_year_segment_from_financial_pdfs(self):
        d = Disclosure(
            document_url="https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024/12345.pdf",
            document_id="12345",
            filing_year=2024,
            is_ptr=False,
        )
        assert (
            build_alt_pdf_url(d)
            == "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/12345.pdf"
        )

    def test_returns_none_for_ptr_urls(self):
        d = Disclosure(
            document_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2024/9.pdf",
            document_id="9",
            filing_year=2024,
            is_ptr=True,
        )
        assert build_alt_pdf_url(d) is None

    def test_returns_none_when_no_year_segment(self):
        # If the URL has no /year/ segment to strip, there's no alt to try.
        d = Disclosure(
            document_url="https://example.com/financial-pdfs/12345.pdf",
            document_id="12345",
            filing_year=2024,
            is_ptr=False,
        )
        assert build_alt_pdf_url(d) is None


# ---------------- record_failed_download ----------------


class TestRecordFailedDownload:
    def test_creates_csv_with_header_on_first_call(self, tmp_path: Path):
        d = Disclosure(
            document_id="DOC1",
            filing_year=2024,
            document_url="https://example.com/x.pdf",
            is_ptr=False,
        )
        reason = record_failed_download(tmp_path, d, "404 Not Found")

        assert reason == "404 Not Found"
        csv_path = tmp_path / "failed_downloads.csv"
        assert csv_path.exists()

        rows = list(csv.reader(csv_path.open()))
        assert rows[0] == [
            "timestamp",
            "document_id",
            "filing_year",
            "is_ptr",
            "document_url",
            "reason",
        ]
        assert rows[1][1:] == [
            "DOC1",
            "2024",
            "False",
            "https://example.com/x.pdf",
            "404 Not Found",
        ]

    def test_appends_without_duplicate_header(self, tmp_path: Path):
        d = Disclosure(
            document_id="DOC1",
            filing_year=2024,
            document_url="x",
            is_ptr=False,
        )
        record_failed_download(tmp_path, d, "first")
        record_failed_download(tmp_path, d, "second")

        rows = list(csv.reader((tmp_path / "failed_downloads.csv").open()))
        # 1 header + 2 data rows
        assert len(rows) == 3
        assert rows[0][0] == "timestamp"
        assert rows[1][-1] == "first"
        assert rows[2][-1] == "second"


# ---------------- fix_future_dates ----------------


class TestFixFutureDatesDirect:
    """The orchestrator wrapper is covered in test_orchestrator.py;
    here we lock the function-level contract."""

    def test_returns_zero_when_nothing_future(self, db_session):
        member = _make_member(db_session)
        db_session.add(
            Disclosure(
                member_id=member.id,
                filing_year=2020,
                filing_type="FD",
                filing_date=datetime(2020, 1, 1),
                document_id="OLD",
                parsed=True,
            )
        )
        db_session.commit()
        assert fix_future_dates(db_session) == 0

    def test_clamps_future_to_now(self, db_session):
        member = _make_member(db_session)
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="FD",
            filing_date=datetime.now() + timedelta(days=400),
            document_id="FUTURE",
            parsed=True,
        )
        db_session.add(d)
        db_session.commit()

        assert fix_future_dates(db_session) == 1
        db_session.refresh(d)
        assert d.filing_date <= datetime.now()
