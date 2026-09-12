"""Committee assignment ingestion.

Runs against fixtures captured from the real upstream files rather than
hand-written YAML, so the parsing is pinned to the shape the source actually
publishes -- including the detail that subcommittee keys in the membership file
are the parent's thomas_id with a numeric suffix appended ("SSAF13").

This replaces `SAMPLE_COMMITTEE_ASSIGNMENTS = {}`, the empty dict that left the
old committee-conflict detector with no committee data at all.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.db.models import Chamber, CommitteeAssignment, Member, Party
from src.ingestion.committees import build_committee_index, ingest_committee_assignments

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return yaml.safe_load((FIXTURES / name).read_text())


@pytest.fixture
def committees_yaml():
    return _fixture("committees-current.yaml")


@pytest.fixture
def membership_yaml():
    return _fixture("committee-membership-current.yaml")


class TestBuildCommitteeIndex:
    def test_indexes_top_level_committees(self, committees_yaml):
        index = build_committee_index(committees_yaml)

        assert "SSAF" in index
        assert index["SSAF"]["is_subcommittee"] is False
        assert index["SSAF"]["parent_committee_id"] is None
        assert "Agriculture" in index["SSAF"]["name"]

    def test_composes_subcommittee_ids_from_the_parent(self, committees_yaml):
        index = build_committee_index(committees_yaml)

        subs = [cid for cid, meta in index.items() if meta["is_subcommittee"]]
        assert subs, "fixture should contain subcommittees"
        for cid in subs:
            parent = index[cid]["parent_committee_id"]
            assert parent is not None
            assert cid.startswith(parent), f"{cid} should start with its parent {parent}"

    def test_maps_chamber(self, committees_yaml):
        index = build_committee_index(committees_yaml)

        assert index["SSAF"]["chamber"] == Chamber.SENATE
        assert index["HSBA"]["chamber"] == Chamber.HOUSE

    def test_joint_committee_has_no_chamber(self, committees_yaml):
        index = build_committee_index(committees_yaml)

        # "joint" is neither house nor senate, so chamber is left unset rather
        # than guessed.
        assert index["JSPR"]["chamber"] is None

    def test_subcommittee_name_includes_the_parent(self, committees_yaml):
        index = build_committee_index(committees_yaml)

        sub = next(cid for cid, m in index.items() if m["is_subcommittee"])
        assert " - " in index[sub]["name"]

    def test_empty_input_is_not_an_error(self):
        assert build_committee_index([]) == {}
        assert build_committee_index(None) == {}


class TestIngestCommitteeAssignments:
    @pytest.fixture
    def seeded(self, db_session, membership_yaml):
        """Create Member rows for the first few bioguide IDs in the fixture."""
        bioguides = []
        for people in membership_yaml.values():
            for person in people or []:
                if person.get("bioguide") not in bioguides:
                    bioguides.append(person["bioguide"])

        for index, bioguide in enumerate(bioguides[:5]):
            db_session.add(
                Member(
                    bioguide_id=bioguide,
                    first_name="Seeded",
                    last_name=f"Member{index}",
                    chamber=Chamber.SENATE,
                    party=Party.REPUBLICAN,
                    state="XX",
                )
            )
        db_session.commit()
        return bioguides[:5]

    def _run(self, db, committees_yaml, membership_yaml):
        with patch("src.ingestion.committees._fetch_yaml") as fetch:
            fetch.side_effect = [committees_yaml, membership_yaml]
            return ingest_committee_assignments(db)

    def test_stores_assignments_for_known_members(
        self, db_session, seeded, committees_yaml, membership_yaml
    ):
        result = self._run(db_session, committees_yaml, membership_yaml)

        assert result["assignments"] > 0
        assert db_session.query(CommitteeAssignment).count() == result["assignments"]

    def test_unknown_members_are_counted_not_stored(
        self, db_session, seeded, committees_yaml, membership_yaml
    ):
        result = self._run(db_session, committees_yaml, membership_yaml)

        # The fixture names more people than were seeded; those must be skipped
        # rather than silently attached to the wrong member.
        assert result["skipped_unknown_member"] > 0
        member_ids = {m.id for m in db_session.query(Member).all()}
        stored = {a.member_id for a in db_session.query(CommitteeAssignment).all()}
        assert stored <= member_ids

    def test_reingest_replaces_rather_than_duplicates(
        self, db_session, seeded, committees_yaml, membership_yaml
    ):
        first = self._run(db_session, committees_yaml, membership_yaml)
        second = self._run(db_session, committees_yaml, membership_yaml)

        assert first["assignments"] == second["assignments"]
        assert db_session.query(CommitteeAssignment).count() == second["assignments"]

    def test_subcommittee_rows_carry_their_parent(
        self, db_session, seeded, committees_yaml, membership_yaml
    ):
        self._run(db_session, committees_yaml, membership_yaml)

        subs = (
            db_session.query(CommitteeAssignment)
            .filter(CommitteeAssignment.is_subcommittee.is_(True))
            .all()
        )
        assert subs, "fixture should produce subcommittee assignments"
        for row in subs:
            assert row.parent_committee_id
            assert row.committee_id.startswith(row.parent_committee_id)

    def test_titles_are_captured(self, db_session, seeded, committees_yaml, membership_yaml):
        self._run(db_session, committees_yaml, membership_yaml)

        titles = {
            a.title for a in db_session.query(CommitteeAssignment).all() if a.title is not None
        }
        assert titles, "leadership titles such as Chairman should be preserved"

    def test_a_member_is_never_double_booked_on_one_committee(
        self, db_session, seeded, committees_yaml, membership_yaml
    ):
        self._run(db_session, committees_yaml, membership_yaml)

        pairs = [(a.member_id, a.committee_id) for a in db_session.query(CommitteeAssignment).all()]
        assert len(pairs) == len(set(pairs))
