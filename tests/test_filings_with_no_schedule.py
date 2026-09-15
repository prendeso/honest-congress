"""203 extension requests were counted as parser failures.

`parse_disclosure` picks its scoring rubric from the file suffix and `is_ptr`,
never from what the filing IS. So every non-PTR House filing went through
`score_fd_parse`, the annual-filing rubric, which asks one question:

    if not assets and not liabilities:
        return ParseConfidence(0.0, ["no assets or liabilities found in an annual filing"])

House filing type **X** is "FDER" -- a Financial Disclosure EXTENSION REQUEST.
One page, roughly a thousand characters, a form letter to the Clerk of the House
asking for more time. It has no assets because it is not a disclosure. Types D
and W are one-page letters under the same "CNRFDR" heading.

Measured over the live corpus: of 422 filings that were readable and yielded
nothing, **211 were these** -- 203 of type X alone, the largest single category
of apparent parser failure in the project. Each carried a `parse_error`, sat in
the "needs attention" queue, and dragged down the number the project uses to
decide whether its parsing is getting better.

The remaining 28 House filings are types O, A, H and T -- real multi-page FDRs
(Rosa DeLauro's is 4 pages and 4,651 characters) that the parser genuinely read
nothing from. Those must keep reporting themselves as failures, which is why
this fix is a closed set of three letters and not a heuristic about page count.

Every filing type here was verified by downloading the document and reading it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.parsing.confidence import (
    NO_FINANCIAL_SCHEDULE,
    score_fd_parse,
    score_filing_with_no_schedule,
)


class TestTheVerdictItself:
    def test_a_letter_with_nothing_to_find_is_a_complete_parse(self):
        score = score_filing_with_no_schedule("X")

        assert score.confidence == 1.0
        assert score.summary is None

    def test_the_annual_rubric_would_have_called_it_a_failure(self):
        """Side by side, because this is the whole change."""
        as_an_annual_filing = score_fd_parse(True, 0, 0, [])

        assert as_an_annual_filing.confidence == 0.0
        assert as_an_annual_filing.summary == "no assets or liabilities found in an annual filing"
        assert score_filing_with_no_schedule("X").confidence == 1.0

    def test_the_set_is_exactly_the_three_verified_letters(self):
        assert NO_FINANCIAL_SCHEDULE == {"X", "D", "W"}

    @pytest.mark.parametrize("filing_type", ["O", "A", "H", "T", "P", "C"])
    def test_a_real_report_is_not_in_it(self, filing_type):
        """O, A, H and T are multi-page FDRs. Adding them here would silence 28
        genuine parser failures, which is the opposite of the point."""
        assert filing_type not in NO_FINANCIAL_SCHEDULE


class TestItReachesTheDatabase:
    def _filing(self, db, filing_type):
        from src.db.models import Chamber, Disclosure, Member, Party

        member = db.query(Member).filter(Member.bioguide_id == "NS00001").first()
        if member is None:
            member = Member(
                bioguide_id="NS00001",
                first_name="No",
                last_name="Schedule",
                chamber=Chamber.HOUSE,
                party=Party.REPUBLICAN,
                state="CA",
            )
            db.add(member)
            db.commit()

        disclosure = Disclosure(
            member_id=member.id,
            filing_year=2025,
            filing_type=filing_type,
            filing_date=datetime(2025, 8, 13),
            document_id=f"NS-{filing_type}-{datetime.now().timestamp()}",
            document_url="https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/1.pdf",
            is_ptr=False,
            parsed=False,
        )
        db.add(disclosure)
        db.commit()
        db.refresh(disclosure)
        return disclosure

    def _parse(self, db, tmp_path, filing_type):
        """Through the REAL parser on a REAL one-page PDF carrying no schedule."""
        from src.ingestion.orchestrator import IngestionOrchestrator
        from tests.test_fd_scan_vs_failure import minimal_pdf

        saved = tmp_path / f"{filing_type}.pdf"
        saved.write_bytes(minimal_pdf("Clerk of the House of Representatives Extension Request"))

        disclosure = self._filing(db, filing_type)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]

        assert orch.parse_disclosure(db, disclosure) is True
        db.refresh(disclosure)
        return disclosure

    @pytest.mark.parametrize("filing_type", ["X", "D", "W"])
    def test_an_extension_request_is_not_flagged_for_attention(
        self, db_session, tmp_path, filing_type
    ):
        disclosure = self._parse(db_session, tmp_path, filing_type)

        assert disclosure.parse_confidence == 1.0
        assert disclosure.parse_error is None, (
            "a one-page letter to the Clerk was sitting in the needs-attention "
            "queue as a failed annual filing"
        )
        assert disclosure.parse_warnings is None

    def test_a_lowercase_or_padded_type_is_still_recognised(self, db_session, tmp_path):
        disclosure = self._parse(db_session, tmp_path, " x ")

        assert disclosure.parse_confidence == 1.0

    def test_a_real_annual_filing_that_yielded_nothing_still_reports_it(self, db_session, tmp_path):
        """Type O is a genuine FDR. If this stops failing, the fix has been
        widened into a way of hiding 28 real parser gaps."""
        disclosure = self._parse(db_session, tmp_path, "O")

        assert disclosure.parse_confidence == 0.0
        assert disclosure.parse_error == "no assets or liabilities found in an annual filing"

    def test_a_scan_is_still_a_scan(self, db_session, tmp_path):
        """The no-schedule verdict must not swallow the scan verdict: a type-O
        filing with no text layer is still an unreadable document."""
        from src.ingestion.orchestrator import IngestionOrchestrator
        from tests.test_fd_scan_vs_failure import minimal_pdf

        saved = tmp_path / "scan.pdf"
        saved.write_bytes(minimal_pdf())

        disclosure = self._filing(db_session, "O")
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]
        orch.parse_disclosure(db_session, disclosure)
        db_session.refresh(disclosure)

        assert disclosure.has_text_layer is False
        assert disclosure.parse_error == "no text layer in PDF - likely a scan"
