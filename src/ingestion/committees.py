"""Committee assignments from unitedstates/congress-legislators.

Public domain, no API key, and keyed on bioguide IDs -- the identifier `Member`
already carries, so no name matching is required.

Two files are needed and they are joined on the committee's thomas_id:

* ``committees-current.yaml``            id -> name, chamber, subcommittees
* ``committee-membership-current.yaml``  id -> [{bioguide, title, rank, party}]

The membership file keys subcommittees as the parent id plus a numeric suffix
("HSAG15"), which is how a subcommittee row finds its parent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import requests
import yaml
from sqlalchemy.orm import Session

from src.db.models import Chamber, CommitteeAssignment, Member

logger = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
COMMITTEES_URL = f"{BASE_URL}/committees-current.yaml"
MEMBERSHIP_URL = f"{BASE_URL}/committee-membership-current.yaml"

REQUEST_TIMEOUT = 60

_CHAMBERS = {"house": Chamber.HOUSE, "senate": Chamber.SENATE}


def _fetch_yaml(url: str) -> Any:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return yaml.safe_load(response.text)


def build_committee_index(committees: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Flatten committees and their subcommittees into one id -> metadata map.

    Subcommittee ids in the membership file are the parent id concatenated with
    the subcommittee's own thomas_id, so they are composed the same way here.
    """
    index: Dict[str, Dict[str, Any]] = {}

    for committee in committees or []:
        committee_id = committee.get("thomas_id")
        if not committee_id:
            continue

        chamber = _CHAMBERS.get(str(committee.get("type", "")).lower())
        index[committee_id] = {
            "name": committee.get("name", committee_id),
            "chamber": chamber,
            "is_subcommittee": False,
            "parent_committee_id": None,
        }

        for sub in committee.get("subcommittees") or []:
            sub_id = sub.get("thomas_id")
            if not sub_id:
                continue
            index[f"{committee_id}{sub_id}"] = {
                "name": f"{committee.get('name', committee_id)} - {sub.get('name', sub_id)}",
                "chamber": chamber,
                "is_subcommittee": True,
                "parent_committee_id": committee_id,
            }

    return index


def ingest_committee_assignments(db: Session) -> Dict[str, int]:
    """Fetch and upsert every current committee assignment.

    Assignments are replaced wholesale rather than merged: membership changes
    between congresses, and a member who leaves a committee should stop being
    reported on it.
    """
    logger.info("Fetching committee metadata...")
    committee_index = build_committee_index(_fetch_yaml(COMMITTEES_URL))
    logger.info("Indexed %d committees and subcommittees", len(committee_index))

    logger.info("Fetching committee membership...")
    membership = _fetch_yaml(MEMBERSHIP_URL) or {}

    members_by_bioguide = {m.bioguide_id: m for m in db.query(Member).all()}

    db.query(CommitteeAssignment).delete()

    inserted = 0
    unknown_member = 0
    unknown_committee = 0
    seen: set[tuple[int, str]] = set()

    for committee_id, people in membership.items():
        meta = committee_index.get(committee_id)
        if meta is None:
            unknown_committee += 1
            continue

        for person in people or []:
            bioguide = person.get("bioguide")
            member = members_by_bioguide.get(bioguide) if bioguide else None
            if member is None:
                unknown_member += 1
                continue

            # The table has a unique index on (member_id, committee_id); the
            # source occasionally repeats a person within one committee block.
            key = (member.id, committee_id)
            if key in seen:
                continue
            seen.add(key)

            db.add(
                CommitteeAssignment(
                    member_id=member.id,
                    committee_id=committee_id,
                    committee_name=meta["name"],
                    chamber=meta["chamber"],
                    is_subcommittee=meta["is_subcommittee"],
                    parent_committee_id=meta["parent_committee_id"],
                    title=person.get("title"),
                    rank=person.get("rank"),
                    party=person.get("party"),
                )
            )
            inserted += 1

    db.commit()

    logger.info(
        "Committee assignments: %d stored, %d skipped (member not in database), "
        "%d skipped (committee not in metadata)",
        inserted,
        unknown_member,
        unknown_committee,
    )
    return {
        "assignments": inserted,
        "skipped_unknown_member": unknown_member,
        "skipped_unknown_committee": unknown_committee,
        "committees_indexed": len(committee_index),
    }
