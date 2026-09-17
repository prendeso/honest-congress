"""Moving one named filing onto one named member, and refusing everything else.

`repair-house-attribution` sweeps: it re-reads the Clerk's index, runs today's
matcher over every stored filing, and moves the ones that disagree. It is
deliberately blind to filings the index parser drops -- candidate reports among
them, because a candidate is not a member and `_parse_xml_index` stops them
attaching in the first place. `test_house_attribution_repair.py` pins that.

The blindness is right at ingest and leaves a gap afterwards. Document 10058876
is a 2024 Candidate Report whose index entry reads

    Last=Begich  First=Nicholas  Suffix=III  StateDst=AK00  FilingType=C

-- the sitting member's own filing, stored against B000315, who disappeared in a
plane crash in October 1972, and the only filing on that member record. The
sweep reports it as "stored but not in the index" and moves on, for ever.

Un-blinding the sweep would have the matcher guess filers for people who are not
members, and that matcher was caught proposing a confident wrong move (David
Scott -> Austin Scott) on live data. So the repair is explicit instead: name the
document, name the member, print everything, write nothing without `--apply`.

These tests are mostly about what it REFUSES. A command that moves published
rows between named people should be hard to point at the wrong thing.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime

import pytest

from src.cli import cmd_move_filing
from src.db.models import Chamber, Disclosure, Member, Party


@pytest.fixture
def roster(db_session, monkeypatch):
    """The Begich pair: the man who died in 1972, and the sitting member."""
    import contextlib

    import src.cli as cli

    dead = Member(
        bioguide_id="B000315",
        first_name="Nicholas",
        last_name="Begich",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="AK",
        in_office=False,
    )
    sitting = Member(
        bioguide_id="B001323",
        first_name="Nicholas",
        last_name="Begich",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="AK",
        in_office=True,
    )
    db_session.add_all([dead, sitting])
    db_session.commit()
    db_session.refresh(dead)
    db_session.refresh(sitting)

    filing = Disclosure(
        member_id=dead.id,
        filing_year=2024,
        filing_type="C",
        filing_date=datetime(2024, 9, 30),
        document_id="10058876",
        parsed=True,
    )
    db_session.add(filing)
    db_session.commit()
    db_session.refresh(filing)

    @contextlib.contextmanager
    def _get_db():
        yield db_session

    monkeypatch.setattr(cli, "get_db", _get_db)
    return {"dead": dead, "sitting": sitting, "filing": filing}


def _run(**kwargs):
    args = Namespace(document_id="10058876", to_bioguide="B001323", apply=False)
    for key, value in kwargs.items():
        setattr(args, key, value)
    return cmd_move_filing(args)


class TestItWritesNothingByDefault:
    def test_a_dry_run_leaves_the_filing_where_it_was(self, db_session, roster, capsys):
        _run()

        db_session.refresh(roster["filing"])
        assert roster["filing"].member_id == roster["dead"].id
        assert "Nothing written" in capsys.readouterr().out

    def test_it_names_both_members_before_moving_anything(self, roster, capsys):
        """The output is the audit trail. Printing only the destination would
        make a wrong move indistinguishable from a right one afterwards."""
        _run()

        out = capsys.readouterr().out
        assert "B000315" in out and "former" in out
        assert "B001323" in out and "sitting" in out
        assert "10058876" in out


class TestItRefusesAnythingItCannotResolve:
    def test_an_unknown_document_moves_nothing(self, db_session, roster, capsys):
        _run(document_id="99999999")

        db_session.refresh(roster["filing"])
        assert roster["filing"].member_id == roster["dead"].id
        assert "No filing with document_id" in capsys.readouterr().out

    def test_an_unknown_member_moves_nothing(self, db_session, roster, capsys):
        _run(to_bioguide="Z999999", apply=True)

        db_session.refresh(roster["filing"])
        assert roster["filing"].member_id == roster["dead"].id
        assert "No member with bioguide_id" in capsys.readouterr().out

    def test_the_database_will_not_let_a_document_id_repeat(self, db_session, roster):
        """The command refuses a `document_id` matching more than one row. That
        branch is defence in depth rather than a live risk: `Disclosure.
        document_id` is `unique=True` (models.py:115), so the database rejects
        the second row first.

        Asserted here rather than deleted, because the command's refusal is only
        redundant for as long as this constraint holds."""
        import sqlalchemy.exc

        db_session.add(
            Disclosure(
                member_id=roster["sitting"].id,
                filing_year=2024,
                filing_type="C",
                filing_date=datetime(2024, 9, 30),
                document_id="10058876",
                parsed=True,
            )
        )
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_moving_a_filing_onto_the_member_it_already_sits_on_is_a_no_op(self, roster, capsys):
        _run(to_bioguide="B000315", apply=True)

        assert "already on" in capsys.readouterr().out


class TestApplyMovesIt:
    def test_the_filing_lands_on_the_named_member(self, db_session, roster, capsys):
        _run(apply=True)

        db_session.refresh(roster["filing"])
        assert roster["filing"].member_id == roster["sitting"].id

    def test_it_says_the_findings_do_not_follow(self, roster, capsys):
        """Assets and liabilities hang off `disclosure_id` and travel with the
        filing. Anomalies carry their own `member_id` and do not, so a caller who
        stops here leaves derived findings on the wrong person."""
        _run(apply=True)

        out = capsys.readouterr().out
        assert "Anomalies did NOT" in out
        assert "analyze" in out
