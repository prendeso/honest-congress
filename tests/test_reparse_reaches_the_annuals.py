"""Correcting the parser is half the job; the corpus still holds the old reads.

Four parser changes landed in a row -- an honest `parse_confidence`, a banded
Schedule A reader, a banded Schedule D reader, and a Senate annual reader that
had never existed. Between them they roughly triple the disclosed value a House
annual yields and give Senate members assets for the first time.

None of that reaches anybody until the filings are read again, and the one
filter built for "re-read what the parser read badly" cannot do it:
**every House annual in the database is stored at confidence 1.0**, because that
was the defect. `--min-confidence 1.0` selects none of them.

`--reparse` has always applied no filter at all, which is the other extreme: it
re-downloads every PTR in the corpus as well, and no parser change touched a
PTR. `--annual-only` is the missing half, and `--dry-run` is how anyone checks
what a set of flags would take before it takes it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db.models import Chamber, Disclosure, Member, Party


@pytest.fixture
def member(db_session):
    member = Member(
        bioguide_id="AN00001",
        first_name="An",
        last_name="Nual",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="GA",
    )
    db_session.add(member)
    db_session.commit()
    return member


def filing(member, document_id: str, *, is_ptr: bool) -> Disclosure:
    return Disclosure(
        member_id=member.id,
        filing_year=2025,
        filing_type="P" if is_ptr else "O",
        filing_date=datetime(2025, 6, 1),
        document_id=document_id,
        document_url=f"https://disclosures-clerk.house.gov/{document_id}.pdf",
        is_ptr=is_ptr,
        parsed=True,
        parse_confidence=1.0,
        has_text_layer=True,
    )


def selected(db, **kwargs):
    from src.ingestion.orchestrator import IngestionOrchestrator

    orchestrator = IngestionOrchestrator.__new__(IngestionOrchestrator)
    return {d.document_id for d in orchestrator._disclosures_to_parse(db, **kwargs)}


class TestAnnualOnly:
    def test_it_takes_the_annuals_and_leaves_the_ptrs(self, db_session, member):
        db_session.add(filing(member, "ANNUAL-1", is_ptr=False))
        db_session.add(filing(member, "PTR-1", is_ptr=True))
        db_session.commit()

        assert selected(db_session, reparse=True, annual_only=True) == {"ANNUAL-1"}

    def test_without_it_a_reparse_takes_everything(self, db_session, member):
        # Which is correct behaviour and the wrong tool for this job: no parser
        # change touched a PTR, and re-downloading them costs the run's time and
        # the Clerk's rate limit for a guaranteed identical result.
        db_session.add(filing(member, "ANNUAL-1", is_ptr=False))
        db_session.add(filing(member, "PTR-1", is_ptr=True))
        db_session.commit()

        assert selected(db_session, reparse=True) == {"ANNUAL-1", "PTR-1"}

    def test_the_confidence_filter_cannot_substitute_for_it(self, db_session, member):
        # The whole reason this exists. A filing stored at 1.0 is `parsed`, so
        # the default filter skips it, and it is not below 1.0, so the filter
        # built for "re-read what the parser read badly" skips it too -- and
        # 927 House annuals were stored at 1.0 while capturing half of
        # Schedule A.
        db_session.add(filing(member, "ANNUAL-1", is_ptr=False))
        db_session.commit()

        assert selected(db_session, annual_only=True) == set()
        assert selected(db_session, annual_only=True, min_confidence=1.0) == set()
        assert selected(db_session, reparse=True, annual_only=True) == {"ANNUAL-1"}

    def test_it_composes_with_the_other_filters(self, db_session, member):
        db_session.add(filing(member, "ANNUAL-2025", is_ptr=False))
        older = filing(member, "ANNUAL-2024", is_ptr=False)
        older.filing_year = 2024
        db_session.add(older)
        db_session.commit()

        assert selected(db_session, reparse=True, annual_only=True, year=2024) == {"ANNUAL-2024"}


class TestTheDryRunWritesNothing:
    def test_it_reports_the_selection_without_parsing(
        self, db_session, member, capsys, monkeypatch
    ):
        import src.cli as cli

        db_session.add(filing(member, "ANNUAL-1", is_ptr=False))
        db_session.add(filing(member, "PTR-1", is_ptr=True))
        db_session.commit()

        from contextlib import contextmanager

        @contextmanager
        def one_session():
            yield db_session

        monkeypatch.setattr(cli, "get_db", one_session)

        def refuse(*args, **kwargs):  # pragma: no cover - the point is it is not called
            raise AssertionError("a dry run downloaded and parsed a filing")

        from src.ingestion.orchestrator import IngestionOrchestrator

        monkeypatch.setattr(IngestionOrchestrator, "parse_disclosures", refuse)

        cli.cmd_parse(
            _Args(dry_run=True, reparse=True, annual_only=True),
        )

        out = capsys.readouterr().out
        assert "1 filing(s) would be re-read" in out
        assert "Nothing was written" in out
        assert "ANNUAL-1" in out
        assert "PTR-1" not in out


class _Args:
    """The flags `cmd_parse` reads, with the defaults argparse would supply."""

    def __init__(self, **overrides):
        self.limit = None
        self.member_id = None
        self.year = None
        self.ptr_only = False
        self.annual_only = False
        self.dry_run = False
        self.reparse = False
        self.failed_only = False
        self.min_confidence = None
        self.delay = 0.0
        self.__dict__.update(overrides)
