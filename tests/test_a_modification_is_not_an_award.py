"""Money added to a contract signed years ago is not a contract being awarded.

`detect_contract_front_runs` kept every positive-dollar USASpending action and
described each one as the company being "awarded a federal contract". Checked
against USASpending, 183 of 437 published findings pointed at a modification.
Alan Armstrong's Textron purchase was published as bought 6 days before an
award; the action was modification P00086 -- $336M of funding -- on Bell
Textron's FLRAA contract, signed 2022-12-05.

USASpending's `Mod` field separates them: "0" is the base award, anything else
modifies it. The ingester always requested the field and kept it only inside
`external_id`.
"""

from __future__ import annotations

import os
import sqlite3
from argparse import Namespace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.analysis.tier2_detectors import detect_contract_front_runs
from src.db.models import (
    Chamber,
    Disclosure,
    GovernmentContract,
    Member,
    Party,
    Transaction,
    TransactionType,
)
from src.ingestion.usaspending import is_modification


class TestTheModNumberSaysWhichItIs:
    @pytest.mark.parametrize("mod", ["0", "00", "000", " 0 ", 0])
    def test_the_base_award_is_not_a_modification(self, mod):
        assert is_modification(mod) is False

    @pytest.mark.parametrize("mod", ["P00086", "A00012", "1", "P00007", "10"])
    def test_a_numbered_action_is(self, mod):
        assert is_modification(mod) is True

    @pytest.mark.parametrize("mod", [None, "", "   "])
    def test_a_blank_is_not_evidence_either_way(self, mod):
        assert is_modification(mod) is None


def _purchase_before(db, *, is_mod, ticker="TXT"):
    member = Member(
        bioguide_id=f"CM{ticker}{is_mod}"[:20],
        first_name="Con",
        last_name="Tract",
        chamber=Chamber.SENATE,
        party=Party.REPUBLICAN,
        state="OK",
    )
    db.add(member)
    db.commit()
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2026,
        filing_type="PTR",
        filing_date=datetime(2026, 7, 21),
        document_id=f"D-{ticker}-{is_mod}",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    awarded = datetime(2026, 4, 2)
    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=awarded - timedelta(days=6),
            transaction_type=TransactionType.PURCHASE,
            description="Textron Inc. Common Stock",
            ticker=ticker,
            owner="Self",
        )
    )
    db.add(
        GovernmentContract(
            ticker=ticker,
            agency="Department of Defense",
            amount=Decimal("336449608.71"),
            awarded_date=awarded,
            is_modification=is_mod,
        )
    )
    db.commit()
    return detect_contract_front_runs(db)


class TestTheDetector:
    def test_funding_added_to_an_old_contract_is_not_an_award(self, db_session):
        assert _purchase_before(db_session, is_mod=True) == []

    def test_a_base_award_still_is(self, db_session):
        assert len(_purchase_before(db_session, is_mod=False)) == 1

    def test_an_action_whose_status_is_unknown_is_kept(self, db_session):
        """The same rule as a missing amount: absence is not evidence."""
        assert len(_purchase_before(db_session, is_mod=None)) == 1

    def test_the_finding_does_not_claim_insider_information(self, db_session):
        (finding,) = _purchase_before(db_session, is_mod=False)
        assert "insider" not in finding["description"]
        assert "coincidence" in finding["description"]


def test_the_retired_sentence_is_purged():
    from src.cli import _SUPERSEDED_WORDING

    assert (
        "contract_front_run",
        "description",
        "clearest insider-information signals",
    ) in _SUPERSEDED_WORDING


@pytest.fixture
def fresh_sqlite(tmp_path, monkeypatch):
    from src.config import get_settings

    path = tmp_path / "migrate.db"
    monkeypatch.setitem(os.environ, "DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_the_migration_reads_the_mod_out_of_the_stored_key(fresh_sqlite):
    """Existing rows are classified from `external_id`, with no re-ingest."""
    from alembic.config import Config

    from alembic import command
    from src.cli import cmd_init

    repo_root = Path(__file__).resolve().parent.parent
    command.upgrade(Config(str(repo_root / "alembic.ini")), "d1a7c39e50b8")

    with sqlite3.connect(fresh_sqlite) as conn:
        for row_id, key in [
            (1, "W58RGZ23C0001|P00086|2026-04-02|336449608.71"),
            (2, "36C10G26F0001|0|2025-11-06|162311369.0"),
            (3, "N0001924C0001||2024-01-01|5.0"),
            (4, None),
        ]:
            conn.execute(
                "insert into government_contracts (id, ticker, source, external_id,"
                " created_at) values (?, 'XYZ', 'usaspending', ?, '2026-01-01')",
                (row_id, key),
            )

    cmd_init(Namespace())

    with sqlite3.connect(fresh_sqlite) as conn:
        flags = dict(
            conn.execute("select id, is_modification from government_contracts order by id")
        )

    assert flags == {1: 1, 2: 0, 3: None, 4: None}
