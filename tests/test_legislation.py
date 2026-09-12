"""The trade-to-legislation detectors.

These are the findings that name a real person next to a real bill, so the
tests are mostly about what the detectors must NOT say:

* A policy area that names a theme rather than an industry -- "Taxation",
  "Commerce" -- must reach no sector. Both are among the most common areas in
  the real data (Taxation is second only to Health), so a loose mapping here
  would flood the output with claims that a tax bill is about a specific
  issuer.
* Cosponsorship must produce nothing. Measured on the live API, one member
  sponsored 71 bills and cosponsored 1,562; a detector treating them alike
  fires on everybody.
* A trade outside the window, or in another sector, must not be swept in by a
  bill that merely exists.

The positive cases are modelled on a real finding the detectors produced
against live data: Senator Cantwell chairs Commerce, Science and
Transportation, sponsored S.4207 under "Science, Technology, Communications",
and the seeded trade fell three days from introduction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analysis.legislation import (
    DEFAULT_WINDOW_DAYS,
    JURISDICTION_ANOMALY_TYPE,
    SPONSORSHIP_ANOMALY_TYPE,
    _parent_committee,
    bills_worth_committee_lookup,
    coverage_report,
    detect_bill_jurisdiction_conflicts,
    detect_sponsorship_conflicts,
)
from src.analysis.sectors import policy_area_sectors
from src.db.models import (
    Bill,
    BillCommittee,
    BillSponsorship,
    Chamber,
    CommitteeAssignment,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

INTRODUCED = datetime(2024, 6, 1)


@pytest.fixture
def member(db_session) -> Member:
    m = Member(
        bioguide_id="T000001",
        first_name="Test",
        last_name="Member",
        chamber=Chamber.SENATE,
        party=Party.DEMOCRAT,
        state="WA",
    )
    db_session.add(m)
    db_session.commit()
    return m


def _trade(db_session, member, ticker, when, description="common stock"):
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=when.year,
        filing_type="PTR",
        filing_date=when + timedelta(days=10),
        document_id=f"DOC{member.id}{ticker}{when.isoformat()}",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(disclosure)
    db_session.commit()
    txn = Transaction(
        disclosure_id=disclosure.id,
        transaction_date=when,
        transaction_type=TransactionType.PURCHASE,
        description=description,
        ticker=ticker,
    )
    db_session.add(txn)
    db_session.commit()
    return txn


def _bill(db_session, policy_area, number="4207", introduced=INTRODUCED, congress=118):
    bill = Bill(
        congress=congress,
        bill_type="s",
        number=number,
        title=f"A bill about {policy_area}",
        policy_area=policy_area,
        introduced_date=introduced,
    )
    db_session.add(bill)
    db_session.commit()
    return bill


def _sponsor(db_session, bill, member, is_sponsor=True):
    db_session.add(BillSponsorship(bill_id=bill.id, member_id=member.id, is_sponsor=is_sponsor))
    db_session.commit()


# --------------------------------------------------------------------------
# The mapping, and its deliberate gaps
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy_area,sectors",
    [
        ("Armed Forces and National Security", {"defense"}),
        ("Health", {"healthcare"}),
        ("Science, Technology, Communications", {"technology", "telecom"}),
        ("Agriculture and Food", {"agriculture"}),
    ],
)
def test_industry_policy_areas_map_to_sectors(policy_area, sectors):
    assert set(policy_area_sectors(policy_area)) == sectors


@pytest.mark.parametrize(
    "policy_area",
    [
        # The second most common area in the real data. A tax bill touches every
        # issuer in the market, so routing it to a sector would let the detector
        # claim a company link it cannot support.
        "Taxation",
        "Commerce",
        "Economics and Public Finance",
        "Environmental Protection",
        "Government Operations and Politics",
        "International Affairs",
        None,
    ],
)
def test_thematic_policy_areas_deliberately_map_to_nothing(policy_area):
    assert policy_area_sectors(policy_area) == frozenset()


@pytest.mark.parametrize(
    "committee_id,parent",
    [("HSBA", "HSBA"), ("HSBA16", "HSBA"), ("hsif03", "HSIF"), (None, None), ("", None)],
)
def test_subcommittee_seats_resolve_to_their_parent(committee_id, parent):
    assert _parent_committee(committee_id) == parent


# --------------------------------------------------------------------------
# sponsorship_conflict
# --------------------------------------------------------------------------


def test_sponsoring_a_bill_and_trading_the_sector_is_flagged(db_session, member):
    bill = _bill(db_session, "Science, Technology, Communications")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=3))

    found = detect_sponsorship_conflicts(db_session)

    assert len(found) == 1
    assert found[0]["anomaly_type"] == SPONSORSHIP_ANOMALY_TYPE
    assert found[0]["member_id"] == member.id
    assert found[0]["tickers"] == ["MSFT"]
    assert found[0]["days_from_introduction"] == 3
    assert "S 4207" in found[0]["title"]


def test_cosponsorship_produces_nothing(db_session, member):
    bill = _bill(db_session, "Science, Technology, Communications")
    _sponsor(db_session, bill, member, is_sponsor=False)
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=3))

    assert detect_sponsorship_conflicts(db_session) == []


def test_a_thematic_policy_area_produces_nothing(db_session, member):
    bill = _bill(db_session, "Taxation")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=3))

    assert detect_sponsorship_conflicts(db_session) == []


def test_a_bill_with_no_policy_area_produces_nothing(db_session, member):
    bill = _bill(db_session, None)
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=3))

    assert detect_sponsorship_conflicts(db_session) == []


def test_a_trade_outside_the_window_is_not_swept_in(db_session, member):
    bill = _bill(db_session, "Science, Technology, Communications")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=DEFAULT_WINDOW_DAYS + 1))

    assert detect_sponsorship_conflicts(db_session) == []


def test_a_trade_in_another_sector_is_not_swept_in(db_session, member):
    bill = _bill(db_session, "Science, Technology, Communications")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "LMT", INTRODUCED + timedelta(days=3), "Lockheed Martin")

    assert detect_sponsorship_conflicts(db_session) == []


def test_the_window_is_symmetric(db_session, member):
    # A trade before introduction suggests anticipating the bill, one after
    # suggests acting on it, and this data distinguishes neither.
    bill = _bill(db_session, "Health")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "PFE", INTRODUCED - timedelta(days=20))

    found = detect_sponsorship_conflicts(db_session)
    assert len(found) == 1
    assert found[0]["days_from_introduction"] == 20


def test_severity_tracks_proximity(db_session, member):
    # Each bill needs its own introduction date far enough from the others that
    # the windows do not overlap. Sharing one date -- as a first draft of this
    # test did -- makes every bill report the same nearest trade, which is the
    # detector behaving correctly and the test asking the wrong question.
    for index, (number, offset) in enumerate((("1", 2), ("2", 20), ("3", 50))):
        introduced = INTRODUCED + timedelta(days=365 * index)
        bill = _bill(db_session, "Health", number=number, introduced=introduced)
        _sponsor(db_session, bill, member)
        _trade(db_session, member, "PFE", introduced + timedelta(days=offset))

    by_days = {
        f["days_from_introduction"]: f["severity"] for f in detect_sponsorship_conflicts(db_session)
    }
    assert by_days[2] == "HIGH"
    assert by_days[20] == "MEDIUM"
    assert by_days[50] == "LOW"


def test_the_description_refuses_to_claim_causation(db_session, member):
    bill = _bill(db_session, "Health")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=3))

    description = detect_sponsorship_conflicts(db_session)[0]["description"]
    assert "does not establish" in description
    # Everything a reader needs to check it on congress.gov themselves.
    assert "S 4207" in description
    assert "2024-06-01" in description
    assert "PFE" in description


# --------------------------------------------------------------------------
# bill_jurisdiction_conflict
# --------------------------------------------------------------------------


def _seat(db_session, member, committee_id, name="Commerce Committee"):
    db_session.add(
        CommitteeAssignment(
            member_id=member.id,
            committee_id=committee_id,
            committee_name=name,
            chamber=Chamber.SENATE,
        )
    )
    db_session.commit()


def _referral(db_session, bill, committee_id, when=INTRODUCED, name="Commerce Committee"):
    db_session.add(
        BillCommittee(
            bill_id=bill.id,
            committee_id=committee_id,
            committee_name=name,
            activity="Referred To",
            activity_date=when,
        )
    )
    db_session.commit()


def test_a_bill_reaching_the_members_committee_is_flagged(db_session, member):
    bill = _bill(db_session, "Science, Technology, Communications")
    _seat(db_session, member, "SSCM")
    _referral(db_session, bill, "SSCM")
    _trade(db_session, member, "MSFT", INTRODUCED + timedelta(days=5))

    found = detect_bill_jurisdiction_conflicts(db_session)

    assert len(found) == 1
    assert found[0]["anomaly_type"] == JURISDICTION_ANOMALY_TYPE
    assert found[0]["committee_id"] == "SSCM"
    assert found[0]["days_from_activity"] == 5


def test_a_subcommittee_seat_counts_as_the_parents_jurisdiction(db_session, member):
    bill = _bill(db_session, "Health")
    _seat(db_session, member, "SSHR12", name="Health Subcommittee")
    _referral(db_session, bill, "SSHR", name="Health, Education, Labor, and Pensions")
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=5))

    assert len(detect_bill_jurisdiction_conflicts(db_session)) == 1


def test_a_referral_to_a_committee_the_member_is_not_on_produces_nothing(db_session, member):
    bill = _bill(db_session, "Health")
    _seat(db_session, member, "SSCM")
    _referral(db_session, bill, "SSHR", name="HELP Committee")
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=5))

    assert detect_bill_jurisdiction_conflicts(db_session) == []


def test_jurisdiction_findings_need_a_dated_referral(db_session, member):
    # The date is the whole difference from committee_jurisdiction_conflict,
    # which makes the same claim about a standing overlap with no event.
    bill = _bill(db_session, "Health")
    _seat(db_session, member, "SSHR")
    db_session.add(BillCommittee(bill_id=bill.id, committee_id="SSHR", activity="Referred To"))
    db_session.commit()
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=5))

    assert detect_bill_jurisdiction_conflicts(db_session) == []


# --------------------------------------------------------------------------
# Narrowing and coverage
# --------------------------------------------------------------------------


def test_only_bills_that_could_produce_a_finding_are_worth_a_lookup(db_session, member):
    # One request per bill against tens of thousands of bills. On live data this
    # narrowed 10,980 unfetched bills to 26.
    worth = _bill(db_session, "Health", number="100")
    thematic = _bill(db_session, "Taxation", number="200")
    untraded = _bill(db_session, "Agriculture and Food", number="300")
    for bill in (worth, thematic, untraded):
        _sponsor(db_session, bill, member)
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=5))

    selected = {b.number for b in bills_worth_committee_lookup(db_session)}
    assert selected == {"100"}


def test_already_fetched_bills_are_not_selected_again(db_session, member):
    bill = _bill(db_session, "Health")
    _sponsor(db_session, bill, member)
    _trade(db_session, member, "PFE", INTRODUCED + timedelta(days=5))
    assert len(bills_worth_committee_lookup(db_session)) == 1

    bill.committees_fetched = True
    db_session.commit()
    assert bills_worth_committee_lookup(db_session) == []


def test_coverage_report_exposes_the_binding_constraint(db_session, member):
    _bill(db_session, "Health", number="1")
    _bill(db_session, "Taxation", number="2")
    _trade(db_session, member, "PFE", INTRODUCED)
    # Not in SECTOR_TICKERS: the ~70-ticker list is what actually bounds these
    # detectors, and an unreported ceiling reads as a clean result.
    _trade(db_session, member, "ZZZZ", INTRODUCED)

    report = coverage_report(db_session)
    assert report["bills"] == 2
    assert report["bills_mapped_to_a_sector"] == 1
    assert report["distinct_traded_tickers"] == 2
    assert report["traded_tickers_with_a_known_sector"] == 1
