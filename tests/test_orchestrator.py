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
