"""Targeted tests for the ingestion orchestrator.

The full orchestrator is large (~900 lines) and most methods make outbound
HTTP calls. These tests pin behavior on the self-contained pieces — DB
maintenance and URL building — so a future decomposition has guardrails.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.db.models import Chamber, Disclosure, Member, Party
from src.ingestion.orchestrator import IngestionOrchestrator


def _make_member(db) -> Member:
    m = Member(
        bioguide_id="O000001",
        first_name="Orch",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


class TestFixFutureDates:
    def test_no_future_dates_returns_zero(self, db_session):
        member = _make_member(db_session)
        db_session.add(
            Disclosure(
                member_id=member.id,
                filing_year=2024,
                filing_type="FD",
                filing_date=datetime(2024, 6, 1),
                document_id="DOC1",
                parsed=True,
            )
        )
        db_session.commit()

        assert IngestionOrchestrator().fix_future_dates(db_session) == 0

    def test_future_dates_clamped_to_now(self, db_session):
        member = _make_member(db_session)
        future = datetime.now() + timedelta(days=365)
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="FD",
            filing_date=future,
            document_id="FUTURE_DOC",
            parsed=True,
        )
        db_session.add(d)
        db_session.commit()

        fixed = IngestionOrchestrator().fix_future_dates(db_session)
        assert fixed == 1

        db_session.refresh(d)
        # Now-clamped, so it should no longer be in the future.
        assert d.filing_date <= datetime.now()

    def test_only_fixes_future_disclosures(self, db_session):
        """Past-dated disclosures must not be touched."""
        member = _make_member(db_session)
        past = datetime(2020, 1, 1)
        future = datetime.now() + timedelta(days=10)

        db_session.add_all(
            [
                Disclosure(
                    member_id=member.id,
                    filing_year=2020,
                    filing_type="FD",
                    filing_date=past,
                    document_id="PAST_DOC",
                    parsed=True,
                ),
                Disclosure(
                    member_id=member.id,
                    filing_year=2024,
                    filing_type="FD",
                    filing_date=future,
                    document_id="FUTURE_DOC",
                    parsed=True,
                ),
            ]
        )
        db_session.commit()

        assert IngestionOrchestrator().fix_future_dates(db_session) == 1
        # Re-running yields zero (idempotent).
        assert IngestionOrchestrator().fix_future_dates(db_session) == 0

        past_d = db_session.query(Disclosure).filter_by(document_id="PAST_DOC").one()
        assert past_d.filing_date == past  # unchanged


class TestSyncSenateDisclosures:
    """The returned count must equal rows actually written.

    This method used to build a Disclosure, leave `db.add` commented out, and
    increment `synced` anyway -- so `run_full_sync` reported Senate filings that
    were never stored, and the CLI printed that number to the operator.
    """

    @staticmethod
    def _senator(db, first="Jane", last="Doe", bioguide="S000001") -> Member:
        m = Member(
            bioguide_id=bioguide,
            first_name=first,
            last_name=last,
            chamber=Chamber.SENATE,
            party=Party.DEMOCRAT,
            state="NY",
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return m

    @staticmethod
    def _row(doc_id="SEN-1", first="Jane", last="Doe") -> dict:
        return {
            "document_id": doc_id,
            "document_url": f"https://efdsearch.senate.gov/{doc_id}",
            "filing_year": 2024,
            "filing_type": "Annual Report",
            "filing_date": datetime(2024, 5, 15),
            "first_name": first,
            "last_name": last,
        }

    def _orchestrator(self, rows):
        orch = IngestionOrchestrator()
        orch.senate.search_all_disclosures = lambda year: rows  # type: ignore[method-assign]
        return orch

    def test_count_matches_rows_actually_persisted(self, db_session):
        self._senator(db_session)
        orch = self._orchestrator([self._row()])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        stored = db_session.query(Disclosure).filter(Disclosure.document_id == "SEN-1").count()
        assert synced == 1
        assert stored == synced, "reported count must equal rows written"

    def test_unmatched_member_is_not_counted(self, db_session):
        # No senator seeded -> nothing can be linked, so nothing is stored.
        orch = self._orchestrator([self._row(first="Nobody", last="Missing")])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).count() == 0

    def test_ambiguous_match_is_skipped_not_misattributed(self, db_session):
        self._senator(db_session, first="Jane", last="Doe", bioguide="S000001")
        self._senator(db_session, first="Janet", last="Doe", bioguide="S000002")
        orch = self._orchestrator([self._row(first="Jan", last="Doe")])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).count() == 0

    def test_existing_document_id_is_not_double_counted(self, db_session):
        senator = self._senator(db_session)
        db_session.add(
            Disclosure(
                member_id=senator.id,
                filing_year=2024,
                filing_type="Annual Report",
                filing_date=datetime(2024, 5, 15),
                document_id="SEN-1",
                document_url="https://efdsearch.senate.gov/SEN-1",
                parsed=False,
            )
        )
        db_session.commit()
        orch = self._orchestrator([self._row()])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).filter(Disclosure.document_id == "SEN-1").count() == 1


class TestScannedFilingsAreRecordedAsSuch:
    """`has_text_layer` is written on every parse, and re-parses skip the scans.

    12.7% of 2024-25 House PTRs are scans with no extractable text. They score
    0.0 and always will. Without the flag, every `--min-confidence` re-parse run
    downloads and re-reads one filing in eight to arrive at the same answer, and
    the failure count reported to a reader blames the parser for the form.
    """

    def _orchestrator(self, tmp_path, *, text_extracted: bool):
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d: tmp_path / "x.pdf"  # type: ignore[method-assign]
        (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
        orch.ptr_parser.parse_ptr = lambda path: {  # type: ignore[method-assign]
            "quality": {
                "text_extracted": text_extracted,
                "rows_detected": 1 if text_extracted else 0,
                "rows_parsed": 1 if text_extracted else 0,
            },
            "transactions": (
                [
                    {
                        "transaction_date": datetime(2024, 3, 1),
                        "transaction_type": "purchase",
                        "amount_min": 1001,
                        "amount_max": 15000,
                        "description": "AAPL",
                        "ticker": "AAPL",
                    }
                ]
                if text_extracted
                else []
            ),
        }
        return orch

    def _ptr(self, db, member, doc_id):
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=datetime(2024, 5, 1),
            document_id=doc_id,
            is_ptr=True,
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return d

    def test_a_scan_is_flagged_and_a_readable_filing_is_not(self, db_session, tmp_path):
        member = _make_member(db_session)

        scan = self._ptr(db_session, member, "SCAN-9")
        self._orchestrator(tmp_path, text_extracted=False).parse_disclosure(db_session, scan)
        assert scan.has_text_layer is False
        assert scan.parse_confidence == 0.0

        readable = self._ptr(db_session, member, "TEXT-9")
        self._orchestrator(tmp_path, text_extracted=True).parse_disclosure(db_session, readable)
        assert readable.has_text_layer is True
        assert readable.parse_confidence and readable.parse_confidence > 0

    def test_a_limited_reparse_advances_instead_of_repeating(self, db_session, tmp_path):
        """`--min-confidence` with `--limit` must reach the whole corpus.

        The query had no ORDER BY, so the database returned an arbitrary set.
        A filing that re-parses to less than the threshold still matches the
        filter, so a limited run took the same rows every time and the rest of
        the corpus was never reached, however many times the job was
        dispatched. Ordering by `updated_at` -- which `onupdate` bumps on every
        parse -- sends a filing to the back of the queue as soon as it is read.
        """
        member = _make_member(db_session)
        for index in range(4):
            filing = self._ptr(db_session, member, f"LOW-{index}")
            filing.parsed, filing.parse_confidence, filing.has_text_layer = True, 0.5, True
            filing.updated_at = datetime(2024, 1, 1 + index)
        db_session.commit()

        # Parse two, then two more. Every filing still scores below 1.0
        # afterwards, so nothing leaves the filter -- only the ordering can
        # make the second run pick up different rows.
        orch = self._orchestrator(tmp_path, text_extracted=True)
        orch.ptr_parser.parse_ptr = lambda path: {  # type: ignore[method-assign]
            "quality": {"text_extracted": True, "rows_detected": 2, "rows_parsed": 1},
            "transactions": [
                {
                    "transaction_date": datetime(2024, 3, 1),
                    "transaction_type": "purchase",
                    "amount_min": 1001,
                    "amount_max": 15000,
                    "description": "AAPL",
                    "ticker": "AAPL",
                }
            ],
        }

        first = {d.document_id for d in self._reparse_batch(db_session, orch, limit=2)}
        second = {d.document_id for d in self._reparse_batch(db_session, orch, limit=2)}

        assert first == {"LOW-0", "LOW-1"}, "oldest first"
        assert second == {"LOW-2", "LOW-3"}, (
            f"the second run repeated {first & second} instead of advancing"
        )

    def _reparse_batch(self, db, orch, *, limit):
        """Run one limited re-parse and report which filings it touched."""
        before = {d.document_id: d.updated_at for d in db.query(Disclosure).all()}
        orch.parse_disclosures(db, min_confidence=1.0, limit=limit, delay=0)
        db.expire_all()
        return [d for d in db.query(Disclosure).all() if d.updated_at != before.get(d.document_id)]

    def test_reparsing_the_low_scorers_leaves_the_scans_alone(self, db_session, tmp_path):
        """A scan scores 0.0 forever. Re-reading it every night buys nothing."""
        member = _make_member(db_session)

        scan = self._ptr(db_session, member, "SCAN-8")
        scan.parsed, scan.parse_confidence, scan.has_text_layer = True, 0.0, False
        broken = self._ptr(db_session, member, "BROKEN-8")
        broken.parsed, broken.parse_confidence, broken.has_text_layer = True, 0.0, True
        never_checked = self._ptr(db_session, member, "OLD-8")
        never_checked.parsed = True
        db_session.commit()

        orch = self._orchestrator(tmp_path, text_extracted=True)
        result = orch.parse_disclosures(db_session, min_confidence=0.8, delay=0)

        assert result["parsed"] == 2, "the broken filing and the never-checked one, not the scan"
        assert scan.parse_confidence == 0.0
        assert scan.has_text_layer is False
