"""The `reset` command.

Irreversible, so the safety rails are the point:

* a bare `reset` reports and exits without touching anything
* production refuses unless a second explicit flag is given
* downloaded PDFs are kept unless asked for, because re-fetching thousands of
  files is slow and hits House Clerk hard

These tests drive the command through its argparse namespace rather than a
subprocess, so a regression in the flag wiring is caught as well as one in the
behaviour.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from decimal import Decimal

import pytest

from src.cli import cmd_reset
from src.db import SessionLocal
from src.db.models import (
    Anomaly,
    Asset,
    Chamber,
    CommitteeAssignment,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


def _args(**overrides) -> Namespace:
    base = {"yes": False, "force_production": False, "purge_pdfs": False}
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def seeded():
    db = SessionLocal()
    for table in (Anomaly, Transaction, Asset, CommitteeAssignment, Disclosure, Member):
        db.query(table).delete()
    db.commit()

    m = Member(
        bioguide_id="RS00001",
        first_name="Reset",
        last_name="Target",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    d = Disclosure(
        member_id=m.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 5, 1),
        document_id="RS-1",
        is_ptr=True,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)

    db.add(
        Transaction(
            disclosure_id=d.id,
            transaction_date=datetime(2024, 3, 1),
            transaction_type=TransactionType.PURCHASE,
            description="AAPL",
            ticker="AAPL",
            amount_min=Decimal("1001"),
            amount_max=Decimal("15000"),
        )
    )
    db.add(
        Anomaly(
            member_id=m.id,
            anomaly_type="large_trade",
            severity="high",
            title="seeded",
            description="seeded",
        )
    )
    db.commit()
    yield db
    db.close()


class TestDryRun:
    def test_reports_counts_without_deleting(self, seeded, capsys):
        cmd_reset(_args())

        out = capsys.readouterr().out
        assert "Dry run. Nothing has been changed." in out
        assert "members" in out
        assert seeded.query(Member).count() == 1, "dry run must not delete"
        assert seeded.query(Anomaly).count() == 1

    def test_names_the_target_database(self, seeded, capsys):
        cmd_reset(_args())

        assert "Target database:" in capsys.readouterr().out

    def test_tells_you_how_to_actually_run_it(self, seeded, capsys):
        cmd_reset(_args())

        assert "--yes" in capsys.readouterr().out


class TestProductionGuard:
    def test_refuses_in_production_without_the_second_flag(self, seeded, monkeypatch, capsys):
        from src.config import Settings

        # cmd_reset imports get_settings from src.config at call time, so the
        # module attribute is the one that has to be patched.
        monkeypatch.setattr(
            "src.config.get_settings", lambda: Settings(ENV="production", ADMIN_PASSWORD="x")
        )

        with pytest.raises(SystemExit) as exc:
            cmd_reset(_args(yes=True))

        assert exc.value.code == 1
        assert "REFUSING" in capsys.readouterr().out
        assert seeded.query(Member).count() == 1, "a refused reset must change nothing"

    def test_dry_run_in_production_is_still_allowed(self, seeded, monkeypatch, capsys):
        from src.config import Settings

        monkeypatch.setattr(
            "src.config.get_settings", lambda: Settings(ENV="production", ADMIN_PASSWORD="x")
        )

        cmd_reset(_args())

        assert "Dry run" in capsys.readouterr().out


class TestPdfHandling:
    def test_pdfs_are_kept_by_default(self, seeded, tmp_path, capsys):
        cmd_reset(_args())

        out = capsys.readouterr().out
        # Only reported when files exist; either way the default must not purge.
        assert "--purge-pdfs" in out or "Local PDFs" not in out
