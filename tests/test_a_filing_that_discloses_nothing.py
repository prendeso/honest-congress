"""A member who holds nothing is not a filing the parser failed to read.

Seven House annuals sat at `parse_confidence` 0.0 carrying "no assets or
liabilities found in an annual filing" -- the same sentence a genuine failure
gets. Every one of them was read perfectly. The documents say so themselves:

    Maxwell Frost     (10068347, 10077999)
    Lauren Boebert    (10070343)
    Rick Crawford     (10067531, 10075175)
    David Valadao     (10067886, 10075195)

Frost's 2024 annual prints "None disclosed." under all nine schedules. Valadao's
prints it under A, B and D while disclosing $28,800 of farm labour income in C,
two positions in E and four privately funded trips in H -- Florence, Lisbon and
two others. None of C, E or H is stored by this project, so his filing yields
nothing either, and that is a fact about what we read, not about the read.

The cost of scoring them 0.0 is the cost the nested asset-code miscount had:
they were the queue. `--min-confidence 0.8` selected them on every cleanup pass,
re-downloading four members' filings from the Clerk for ever to arrive at the
same answer, and the "yielded nothing at all" figure on the parsed-data page
counted four honest filers as parser failures.

This is `score_filing_with_no_schedule` one case along. An extension request is
not an annual that failed; neither is an annual that discloses nothing.
"""

from __future__ import annotations

from src.parsing.confidence import score_fd_parse
from src.parsing.pdf_parser import discloses_no_rows

# The shape `clean_text` leaves: "SCHEDULE A: ASSETS AND "UNEARNED" INCOME"
# survives cleaning as `S        A: A          "U       " I`.
FROST = """Filing ID #10068347
Name: Hon. Maxwell Alejandro Frost
Filing Type: Annual Report
S        A: A          "U       " I
None disclosed.
S        B: T
None disclosed.
S        C: E      I
None disclosed.
S        D: L
None disclosed.
S        E: P
None disclosed.
"""

VALADAO = """Filing ID #10067886
Name: Hon. David G. Valadao
Filing Type: Annual Report
S        A: A          "U       " I
None disclosed.
S        B: T
None disclosed.
S        C: E      I
Source Type Amount
Valadao Dairy Farm labor $28,800.00
Jackson Dairy, LLC Spouse salary N/A
S        D: L
None disclosed.
S        E: P
Position Name of Organization
Employee Valadao Dairy
S        H: T      P            R
Source Start Date End Date Itinerary Days at Own Exp.
Center Forward 06/14/2024 06/21/2024 Washington, DC - Lisbon, PT 3
"""

HOLDS_THINGS = """Filing ID #10066714
S        A: A          "U       " I
Asset Owner Value of Asset Income Type(s) Income Tx. >
Guardian Point Capital [HE] $5,000,001 - $25,000,000
S        B: T
None disclosed.
S        D: L
None disclosed.
"""


class TestTheDocumentSaysSoItself:
    def test_a_filing_with_nothing_in_any_schedule(self):
        assert discloses_no_rows(FROST) is True

    def test_a_filing_empty_only_where_we_read_it(self):
        # A, B and D are the three schedules that produce stored rows. Valadao
        # discloses plenty in C, E and H; none of it can become an Asset,
        # Transaction or Liability, so zero stored rows is still the whole truth
        # about what was read.
        assert discloses_no_rows(VALADAO) is True

    def test_a_filing_that_holds_something_is_not_empty(self):
        assert discloses_no_rows(HOLDS_THINGS) is False

    def test_one_schedule_with_rows_is_enough_to_say_no(self):
        assert (
            discloses_no_rows(
                VALADAO.replace(
                    "S        B: T\nNone disclosed.",
                    "S        B: T\nDate Tx. Amount\n01/02/2024 P $1,001 - $15,000",
                )
            )
            is False
        )

    def test_a_schedule_that_cannot_be_found_is_not_an_empty_one(self):
        # The affirmative half, deliberately. An absence of rows is not evidence
        # of anything; the form printing its own "None disclosed." is. A
        # document whose headings were mangled past recognition still scores as
        # the failure it is rather than being waved through at 1.0.
        assert discloses_no_rows(FROST.replace("S        D: L", "GARBLED")) is False
        assert discloses_no_rows("") is False
        assert discloses_no_rows("Filing ID #1\nnothing that looks like a schedule") is False

    def test_a_ptr_is_never_read_as_an_empty_annual(self):
        # PTRs carry no lettered schedules at all. `score_fd_parse` is not the
        # scorer for them, but a predicate that said True here would be one
        # wiring mistake away from passing every empty PTR as complete.
        assert discloses_no_rows("Filing ID #20024660\nTransactions\nNone.") is False


class TestWhatItScores:
    def _score(self, **kwargs):
        defaults = dict(text_extracted=True, assets=0, liabilities=0, errors=(), rows_detected=0)
        defaults.update(kwargs)
        return score_fd_parse(**defaults)

    def test_an_honest_empty_filing_is_a_complete_parse(self):
        score = self._score(discloses_no_rows=True)
        assert score.confidence == 1.0
        assert "discloses no assets" in score.summary

    def test_without_the_documents_word_it_is_still_a_failure(self):
        # The case this must not swallow: the parser found nothing and the
        # document never said there was nothing to find.
        score = self._score(discloses_no_rows=False)
        assert score.confidence == 0.0
        assert "no assets or liabilities found" in score.summary

    def test_a_parse_error_still_wins(self):
        # "None disclosed." and a parser that fell over partway through: the
        # document's claim is about the document, not about how far the reader
        # got before it failed.
        score = self._score(discloses_no_rows=True, errors=["page 2 raised"])
        assert score.confidence == 0.0
        assert "page 2 raised" in score.summary

    def test_a_scan_still_wins(self):
        # There is no text layer, so there is no "None disclosed." to have read.
        score = self._score(text_extracted=False, discloses_no_rows=True)
        assert score.confidence == 0.0
        assert "scan" in score.summary

    def test_it_does_not_touch_a_filing_that_stored_rows(self):
        # The flag is only consulted when nothing was stored, so it cannot
        # rescue a filing that stored 4 of 8 holdings.
        assert self._score(assets=4, rows_detected=8, discloses_no_rows=True).confidence == 0.5

    def test_the_default_is_the_old_behaviour(self):
        # Callers that cannot read the document -- the Senate path, tests --
        # must keep scoring an empty parse as a failure rather than silently
        # gaining a pass.
        assert score_fd_parse(True, 0, 0).confidence == 0.0


class TestItIsWiredIntoTheParse:
    """The predicate exists, and something has to call it.

    This project has landed a correct fix connected to nothing before --
    `purge-stale-wording` was written, tested, merged and wired into no
    workflow, and 425 findings were served for three pipeline runs with text no
    detector could write. So this asserts the stored `parse_confidence`, which
    is what a cleanup pass and the parsed-data page actually read, rather than
    the scorer in isolation.
    """

    def _orchestrator(self, tmp_path, raw_text: str):
        from src.ingestion.orchestrator import IngestionOrchestrator

        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d: tmp_path / "fd.pdf"  # type: ignore[method-assign]
        (tmp_path / "fd.pdf").write_bytes(b"%PDF-1.4")
        orch.disclosure_parser.parse_pdf = lambda path: {  # type: ignore[method-assign]
            "assets": [],
            "liabilities": [],
            "transactions": [],
            "parse_errors": [],
            "raw_text": raw_text,
        }
        return orch

    def _annual(self, db, member, doc_id):
        from datetime import datetime

        from src.db.models import Disclosure

        filing = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="O",
            filing_date=datetime(2025, 5, 13),
            document_id=doc_id,
            document_url=f"https://disclosures-clerk.house.gov/{doc_id}.pdf",
            is_ptr=False,
        )
        db.add(filing)
        db.commit()
        db.refresh(filing)
        return filing

    def _member(self, db):
        from src.db.models import Chamber, Member, Party

        member = Member(
            bioguide_id="ND0001",
            first_name="None",
            last_name="Disclosed",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="FL",
        )
        db.add(member)
        db.commit()
        return member

    def test_an_empty_filing_is_stored_as_a_complete_parse(self, db_session, tmp_path):
        member = self._member(db_session)
        filing = self._annual(db_session, member, "FROST-2024")

        self._orchestrator(tmp_path, FROST).parse_disclosure(db_session, filing)

        assert filing.parse_confidence == 1.0, (
            f"stored {filing.parse_confidence} with warnings {filing.parse_warnings!r} -- "
            "the predicate is not reaching the scorer"
        )

    def test_a_filing_that_says_nothing_is_still_a_failure(self, db_session, tmp_path):
        # The guard against the test above passing because everything scores
        # 1.0 now. Same empty parser output, a document that never claims to be
        # empty.
        member = self._member(db_session)
        filing = self._annual(db_session, member, "SILENT-2024")

        self._orchestrator(tmp_path, "Filing ID #1\nsome text with no schedules").parse_disclosure(
            db_session, filing
        )

        assert filing.parse_confidence == 0.0

    def test_it_is_not_selected_by_a_cleanup_pass_any_more(self, db_session, tmp_path):
        # The whole point. `--min-confidence 0.8` selected these seven on every
        # pass, re-downloading four members' filings from the Clerk for ever to
        # arrive at the same answer.
        member = self._member(db_session)
        filing = self._annual(db_session, member, "FROST-2025")
        self._orchestrator(tmp_path, FROST).parse_disclosure(db_session, filing)

        from src.ingestion.orchestrator import IngestionOrchestrator

        orch = IngestionOrchestrator.__new__(IngestionOrchestrator)
        selected = orch._disclosures_to_parse(db_session, annual_only=True, min_confidence=0.8)
        assert [d.document_id for d in selected] == []


# The candidate / amendment / new-filer variant. NO Schedule B at all -- the
# period it covers predates the filer holding office, so the form never asks
# for transactions -- and a Schedule J the annual form does not have.
SOLIS_CANDIDATE = """Filing ID #10072571
Name: Hilda Solis
Status: Congressional Candidate
Filing Type: Candidate Report
Period Covered: 01/01/2024- 11/25/2025
S        A: A          "U       " I
None disclosed.
S        C: E      I
None disclosed.
S        D: L
None disclosed.
S        E: P
Position Name of Organization
Los Angeles County Supervisor, First District County of Los Angeles
S        F: A
None disclosed.
S        J: C               E         $5,000 P       O   S
None disclosed.
"""

NEW_FILER_WITH_HOLDINGS = """Filing ID #8220832
Filing Type: New Filer Report
S        A: A          "U       " I
Asset Owner Value of Asset Income Type(s) Income
Some Fund [MF] $1,000,001 - $5,000,000
S        C: E      I
None disclosed.
S        D: L
None disclosed.
S        J: C               E         $5,000 P       O   S
None disclosed.
"""


class TestTheFormVariantDecidesWhichSchedulesToAskFor:
    """Not every House FD prints the same schedules.

    Surveyed across 28 real documents: the Annual Report prints A-I; the
    Candidate, Amendment and New Filer reports print A C D E F J -- **no
    Schedule B**, because the period they cover predates the filer holding
    office, so the form never asks about transactions.

    Requiring B of all of them was wrong in exactly the way this module keeps
    being wrong. Three filings -- Hilda Solis's 2025 candidate report, her
    amendment, and her 2026 candidate report -- print "None disclosed." under
    every schedule that could produce a stored row, and still scored 0.0 after
    the fix that was supposed to cover them.
    """

    def test_a_candidate_report_has_no_schedule_b_to_require(self):
        assert discloses_no_rows(SOLIS_CANDIDATE) is True

    def test_an_amendment_report_is_the_same_shape(self):
        assert discloses_no_rows(SOLIS_CANDIDATE.replace("Candidate Report", "Amendment Report"))

    def test_a_new_filer_report_is_too(self):
        assert discloses_no_rows(SOLIS_CANDIDATE.replace("Candidate Report", "New Filer Report"))

    def test_a_variant_that_holds_something_is_still_not_empty(self):
        # These are real filings that carry holdings -- the New Filer Report
        # sampled from the Clerk lists 745 of them. Dropping Schedule B from
        # the requirement must not drop Schedule A with it.
        assert discloses_no_rows(NEW_FILER_WITH_HOLDINGS) is False

    def test_an_annual_report_still_has_to_answer_for_schedule_b(self):
        # The safety this trades against. An annual whose Schedule B heading
        # was lost is a document we did not read, not an empty one.
        assert discloses_no_rows(FROST.replace("S        B: T", "GARBLED")) is False

    def test_an_unrecognised_variant_is_asked_for_everything(self):
        # A form shape nobody has looked at reports itself as unread rather
        # than being waved through on a guess. It has no Schedule B, so
        # requiring one makes it fail -- which is the conservative answer.
        assert (
            discloses_no_rows(SOLIS_CANDIDATE.replace("Candidate Report", "Periodic Whatsit"))
            is False
        )

    def test_a_document_with_no_filing_type_line_is_asked_for_everything(self):
        stripped = "\n".join(
            line for line in SOLIS_CANDIDATE.splitlines() if not line.startswith("Filing Type:")
        )
        assert discloses_no_rows(stripped) is False

    def test_schedule_j_bounds_a_region_like_any_other_heading(self):
        # `_ANY_SCHEDULE_HEADING` stopped at I, so on this variant every region
        # search ran straight past Schedule J to the end of the document.
        from src.parsing.pdf_parser import _schedule_region

        region = _schedule_region(SOLIS_CANDIDATE, "F")
        assert region is not None
        assert "C               E         $5,000" not in region
