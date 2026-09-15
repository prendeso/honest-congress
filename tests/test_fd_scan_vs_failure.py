"""An annual filing the parser failed on was stored as a scan.

`score_fd_parse` has two branches for a filing that yielded nothing, and they
mean opposite things:

    "no text layer in PDF - likely a scan"              <- nothing to read
    "no assets or liabilities found in an annual filing" <- the parser's failure

`orchestrator.parse_disclosure` chose between them with

    text_extracted = bool(parsed.get("raw_text") or assets or liabilities)

and `raw_text` was never set — the key occurred exactly once in the repository,
on that line. `DisclosureParser.parse_pdf` built its result without it. So the
expression was `bool(assets or liabilities)`: false precisely when both counts
were zero, which is the condition the *second* branch tests. The first branch
therefore always won and the second was unreachable from the orchestrator.

Every readable annual filing the parser failed on was recorded as a scan — the
verdict that says nobody is at fault and there is nothing to investigate. It is
hidden a second time in `parse_quality_summary`, whose "the parser's own
failure" count filters `has_text_layer` to True/NULL and so excludes them by
construction.

Measured over 40 randomly sampled type-O House annual filings (the sitting
member's own, which is where `Asset.value_min/value_max` and therefore every
wealth-growth finding comes from): 34 read, **3 genuine scans, 3 of these**.
Half of what the database called scans were not scans.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.parsing.confidence import score_fd_parse

SCAN = "no text layer in PDF - likely a scan"
NOTHING_FOUND = "no assets or liabilities found in an annual filing"


class TestParsePdfReportsWhetherTheDocumentHadText:
    """The parser has the text in hand; it simply never passed it back."""

    def test_the_result_carries_the_key_the_orchestrator_reads(self):
        from src.parsing.pdf_parser import DisclosureParser

        result = DisclosureParser().parse_pdf("/nonexistent/missing.pdf")

        assert "raw_text" in result, (
            "orchestrator.parse_disclosure reads parsed['raw_text'] to decide "
            "has_text_layer; without the key it silently decides on the asset count"
        )


class TestTheTwoVerdictsAreBothReachable:
    """Both branches of score_fd_parse, driven the way the orchestrator drives
    them. This is the assertion the bug failed: the second was dead code."""

    def _as_the_orchestrator_does(self, parsed: dict) -> bool:
        from src.ingestion.orchestrator import _fd_text_extracted

        return _fd_text_extracted(parsed)

    def test_a_readable_filing_that_yielded_nothing_is_not_a_scan(self):
        parsed = {
            "raw_text": "SCHEDULE A -- ASSETS AND UNEARNED INCOME\nPage 1 of 4\n",
            "assets": [],
            "liabilities": [],
            "parse_errors": [],
        }

        text_extracted = self._as_the_orchestrator_does(parsed)
        score = score_fd_parse(text_extracted, 0, 0, [])

        assert text_extracted is True
        assert score.summary == NOTHING_FOUND
        assert SCAN not in (score.summary or "")

    def test_a_genuine_scan_still_says_scan(self):
        # parse_pdf accumulates one newline per page, so an image-only PDF
        # comes back as whitespace rather than "". A bare bool() on that is
        # true, which would flip every scan the other way.
        parsed = {"raw_text": "\n\n\n\n", "assets": [], "liabilities": [], "parse_errors": []}

        text_extracted = self._as_the_orchestrator_does(parsed)
        score = score_fd_parse(text_extracted, 0, 0, [])

        assert text_extracted is False
        assert score.summary == SCAN

    def test_the_two_verdicts_are_different(self):
        """They were the same string in practice, because only one was reachable."""
        readable = score_fd_parse(True, 0, 0, [])
        scanned = score_fd_parse(False, 0, 0, [])

        assert readable.summary != scanned.summary

    @pytest.mark.parametrize("assets,liabilities", [(1, 0), (0, 1), (2, 3)])
    def test_a_filing_that_yielded_rows_is_readable_either_way(self, assets, liabilities):
        """The old expression got this case right, and it must stay right."""
        parsed = {
            "raw_text": "",
            "assets": [{}] * assets,
            "liabilities": [{}] * liabilities,
            "parse_errors": [],
        }

        assert self._as_the_orchestrator_does(parsed) is True


def minimal_pdf(text: str = "") -> bytes:
    """A one-page PDF, built by hand so no test-only dependency is needed.

    With `text` it has a text layer; without, it is blank in the way an
    image-only scan is blank to pdfplumber. Verified against pdfplumber:
    612 bytes extracting the string, 543 bytes extracting "".
    """
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode() if text else b"\n"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


class TestTheFailureReachesTheDatabaseAsAFailure:
    """End to end through the REAL parser, which is the point.

    An earlier version of this stubbed `parse_pdf` to return a dict carrying
    `raw_text`, and passed against the bug -- because the bug is precisely that
    the real parser never put the key there. A stub that supplies it tests the
    orchestrator against a parser that does not exist.
    """

    def _annual_filing(self, db):
        from src.db.models import Chamber, Disclosure, Member, Party

        member = Member(
            bioguide_id="FD00001",
            first_name="An",
            last_name="Nual",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="OK",
        )
        db.add(member)
        db.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2025,
            filing_type="O",
            filing_date=datetime(2025, 8, 13),
            document_id="FD-1",
            document_url="https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/1.pdf",
            is_ptr=False,
            parsed=False,
        )
        db.add(disclosure)
        db.commit()
        db.refresh(disclosure)
        return disclosure

    def _parse_real_pdf(self, db, tmp_path, pdf_bytes):
        from src.ingestion.orchestrator import IngestionOrchestrator

        saved = tmp_path / "FD-1.pdf"
        saved.write_bytes(pdf_bytes)

        disclosure = self._annual_filing(db)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]

        assert orch.parse_disclosure(db, disclosure) is True
        db.refresh(disclosure)
        return disclosure

    def test_a_readable_filing_is_not_stored_as_a_scan(self, db_session, tmp_path):
        """Text the parser can read, but nothing it recognises as an asset."""
        disclosure = self._parse_real_pdf(
            db_session, tmp_path, minimal_pdf("SCHEDULE A ASSETS AND UNEARNED INCOME")
        )

        assert disclosure.has_text_layer is True, (
            "stored as a scan, so parse_quality_summary's failure count excludes "
            "it and nobody ever looks at the filing again"
        )
        assert disclosure.parse_error == NOTHING_FOUND

    def test_a_genuine_scan_is_still_stored_as_a_scan(self, db_session, tmp_path):
        disclosure = self._parse_real_pdf(db_session, tmp_path, minimal_pdf())

        assert disclosure.has_text_layer is False
        assert disclosure.parse_error == SCAN
