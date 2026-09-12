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
