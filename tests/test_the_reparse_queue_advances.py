"""A re-parse that changes nothing must still move the filing out of the queue.

`_disclosures_to_parse` orders the re-parse queue by `updated_at` ascending, and
`Disclosure.updated_at` carries `onupdate=datetime.utcnow`. But `onupdate` only
fires when SQLAlchemy actually emits an UPDATE, and it does not emit one when
every assignment lands the value that was already there.

So a filing whose re-read changes none of its OWN columns stays at the front of
the queue for ever, and every subsequent dispatch takes the same rows.

Measured in production before this was fixed: four `reparse-annuals` dispatches
in a row reported

    Trips stored: 303 across 157 filings

without moving, because all four re-read one identical batch of 400. Roughly
75 minutes of runner time and 1,200 redundant downloads from the Clerk, while
the campaign looked like it was progressing.

The earlier confidence campaign never hit it, and that is the trap: it was
CHANGING `parse_confidence` on nearly every filing, so the row was dirty as a
side effect and the queue advanced by luck. This bug only appears when a parser
change affects ANOTHER table -- Schedule H travel rows here -- and leaves every
`Disclosure` column identical.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.db.models import Chamber, Disclosure, Member, Party

# A filing that parses cleanly to nothing: every `Disclosure` column it writes
# is the same on the second pass as on the first.
UNCHANGING = (
    "Filing Type: Annual Report\n"
    "S        A: A\nNone disclosed.\n"
    "S        B: T\nNone disclosed.\n"
    "S        D: L\nNone disclosed.\n"
)


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="RQ00001",
        first_name="Re",
        last_name="Parse",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="TX",
    )
    db_session.add(m)
    db_session.commit()
    return m


def annual(db, member, doc_id):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="O",
        filing_date=datetime(2025, 5, 1),
        document_id=doc_id,
        is_ptr=False,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def orchestrator(tmp_path):
    from src.ingestion.orchestrator import IngestionOrchestrator

    orch = IngestionOrchestrator(data_dir=tmp_path)
    (tmp_path / "fd.pdf").write_bytes(b"%PDF-1.4")
    orch.download_disclosure_pdf = lambda d: Path(tmp_path) / "fd.pdf"  # type: ignore[method-assign]
    orch.disclosure_parser.parse_pdf = lambda path: {  # type: ignore[method-assign]
        "assets": [],
        "liabilities": [],
        "transactions": [],
        "travel_payments": [],
        "parse_errors": [],
        "raw_text": UNCHANGING,
    }
    return orch


class TestAFilingLeavesTheQueueOnceItIsRead:
    def test_a_second_identical_parse_still_moves_updated_at(self, db_session, member, tmp_path):
        filing = annual(db_session, member, "RQ-1")
        orch = orchestrator(tmp_path)

        orch.parse_disclosure(db_session, filing)
        db_session.refresh(filing)
        first = filing.updated_at

        orch.parse_disclosure(db_session, filing)
        db_session.refresh(filing)

        assert filing.updated_at > first, (
            "nothing about the filing changed, so no UPDATE was emitted and "
            "`onupdate` never fired -- this row stays at the front of the "
            "re-parse queue for ever"
        )

    def test_the_queue_hands_out_the_next_batch_not_the_same_one(
        self, db_session, member, tmp_path
    ):
        """The behaviour that actually matters, asserted end to end.

        A limited re-parse must reach the whole corpus across dispatches. With
        the queue frozen, `--limit 1` returns the same filing every time and the
        rest is never read however many times the job is dispatched.
        """
        from src.ingestion.orchestrator import IngestionOrchestrator

        first, second = annual(db_session, member, "RQ-A"), annual(db_session, member, "RQ-B")
        orch = orchestrator(tmp_path)

        # Both already read once, which is the state a campaign actually starts
        # from. The FIRST parse of a filing always dirties the row -- `parsed`
        # goes False to True and `parse_confidence` None to a number -- so it is
        # only the second read that emits no UPDATE, and only then that the
        # queue can freeze.
        orch.parse_disclosure(db_session, first)
        orch.parse_disclosure(db_session, second)
        first.updated_at = datetime(2024, 1, 1)
        second.updated_at = datetime(2024, 1, 2)
        db_session.commit()

        picker = IngestionOrchestrator.__new__(IngestionOrchestrator)

        (one,) = picker._disclosures_to_parse(db_session, reparse=True, annual_only=True, limit=1)
        assert one.document_id == "RQ-A"
        orch.parse_disclosure(db_session, one)

        (two,) = picker._disclosures_to_parse(db_session, reparse=True, annual_only=True, limit=1)
        assert two.document_id == "RQ-B", (
            "the second dispatch took the same filing again; a limited campaign "
            "can never reach the rest of the corpus"
        )

    def test_a_filing_the_parser_could_not_read_still_leaves_the_queue(
        self, db_session, member, tmp_path
    ):
        # Otherwise a scan or a broken document becomes a permanent roadblock:
        # it fails identically every time, so it never moves, and it occupies a
        # slot in every dispatch for ever.
        filing = annual(db_session, member, "RQ-2")
        orch = orchestrator(tmp_path)
        orch.disclosure_parser.parse_pdf = lambda path: {  # type: ignore[method-assign]
            "assets": [],
            "liabilities": [],
            "transactions": [],
            "travel_payments": [],
            "parse_errors": [],
            "raw_text": "",
        }

        orch.parse_disclosure(db_session, filing)
        db_session.refresh(filing)
        first = filing.updated_at
        assert filing.parse_confidence == 0.0

        orch.parse_disclosure(db_session, filing)
        db_session.refresh(filing)
        assert filing.updated_at > first
