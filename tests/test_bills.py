"""Congress.gov bill ingestion, pinned to real API responses.

Fixtures in `tests/fixtures/congress/` are captured verbatim. Each carries a
case that only real data produced:

* `sponsored_legislation.json` -- seven of Pelosi's bills, chosen to cover all
  three outcomes: two with **no policy area at all**, three in a sector-mapped
  area (Health), and two in Taxation, which is deliberately left unmapped
  because it touches every issuer in the market.
* `committees_hr1.json` -- H.R. 1 of the 118th, referred to five full
  committees, **six of which carry nested subcommittees with their own
  systemCodes and their own, later, activity dates**. An ingester reading only
  the top-level list silently drops the sharper signal.
* `committees_hr4644.json` -- the single-committee case, and the one whose
  systemCode (`hswm00`) pins the conversion to congress-legislators' `HSWM`.

The systemCode conversion is the load-bearing join in this module. Congress.gov
says `hswm00` and `hsif03`; congress-legislators -- and therefore
`CommitteeAssignment.committee_id`, which the jurisdiction detector matches on
-- says `HSWM` and `HSIF03`. Both spellings were read off the live sources
rather than inferred from one of them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from src.db.models import Bill, BillCommittee, BillSponsorship, Chamber, Disclosure, Member, Party
from src.ingestion.bills import (
    CONGRESS_API_BASE_URL,
    PAGE_SIZE,
    CongressAPIClient,
    _walk_committees,
    fetch_bill_committees,
    ingest_member_bills,
    system_code_to_thomas_id,
)
from src.ingestion.rate_limit import RequestBudgetExhausted

CONGRESS = Path(__file__).parent / "fixtures" / "congress"

API_KEY = "test-key-not-a-real-one"


def _response(payload, status_code: int = 200, headers: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=status_code)
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _client(responses, **kwargs) -> CongressAPIClient:
    session = MagicMock()
    session.get.side_effect = [_response(p) for p in responses]
    kwargs.setdefault("sleeper", lambda _: None)
    return CongressAPIClient(API_KEY, session=session, **kwargs)


@pytest.fixture
def sponsored() -> dict:
    return json.loads((CONGRESS / "sponsored_legislation.json").read_text())


@pytest.fixture
def cosponsored() -> dict:
    return json.loads((CONGRESS / "cosponsored_legislation.json").read_text())


@pytest.fixture
def committees_hr1() -> dict:
    return json.loads((CONGRESS / "committees_hr1.json").read_text())


@pytest.fixture
def committees_hr4644() -> dict:
    return json.loads((CONGRESS / "committees_hr4644.json").read_text())


def _member(db, bioguide: str = "P000197", chamber=Chamber.HOUSE) -> Member:
    member = Member(
        bioguide_id=bioguide,
        first_name="Test",
        last_name=bioguide,
        chamber=chamber,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(member)
    db.commit()
    return member


# --------------------------------------------------------------------------
# The systemCode join
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system_code,expected",
    [
        ("hswm00", "HSWM"),  # full committee: drop the "00"
        ("hsba00", "HSBA"),
        ("hsif03", "HSIF03"),  # subcommittee: keep its number
        ("hspw14", "HSPW14"),
        ("HSWM00", "HSWM"),
        ("  hswm00  ", "HSWM"),
        (None, None),
        ("", None),
    ],
)
def test_system_code_converts_to_a_congress_legislators_id(system_code, expected):
    assert system_code_to_thomas_id(system_code) == expected


def test_subcommittees_are_walked_not_dropped(committees_hr1):
    walked = list(_walk_committees(committees_hr1["committees"]))
    codes = [c["systemCode"] for c in walked]

    # Five full committees appear in the top-level list...
    assert len([c for c in codes if c.endswith("00")]) == 5
    # ...and the subcommittees nested inside them must come through too.
    assert "hsif03" in codes
    assert "hspw02" in codes
    assert len(walked) > 5


def test_subcommittees_inherit_their_parents_chamber(committees_hr1):
    # Nested entries carry no chamber of their own, and a null there would make
    # the stored row less useful than the response it came from.
    walked = {c["systemCode"]: c for c in _walk_committees(committees_hr1["committees"])}
    assert walked["hsif03"]["chamber"] == "House"


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


def test_client_requires_a_key():
    with pytest.raises(ValueError, match="CONGRESS_GOV_API_KEY"):
        CongressAPIClient("")


def test_key_goes_in_the_query_not_the_path(sponsored):
    client = _client([sponsored])
    client.get("/member/P000197/sponsored-legislation")
    args, kwargs = client.session.get.call_args
    assert args[0].startswith(CONGRESS_API_BASE_URL)
    assert "api_key" not in args[0]
    assert kwargs["params"]["api_key"] == API_KEY
    assert kwargs["params"]["format"] == "json"


def test_pagination_tracks_its_own_offset(sponsored):
    # The API hands back a `pagination.next` URL that already carries the key.
    # Following it would put the credential in anything that logs a URL, so the
    # offset is tracked here instead.
    first = {**sponsored, "pagination": {"next": "https://api.congress.gov/...&api_key=secret"}}
    second = {**sponsored, "pagination": {}}
    client = _client([first, second])

    rows = list(client.paginate("/member/P000197/sponsored-legislation", "sponsoredLegislation"))

    assert len(rows) == 2 * len(sponsored["sponsoredLegislation"])
    assert client.session.get.call_args_list[1][1]["params"]["offset"] == PAGE_SIZE


def test_pagination_stops_on_an_empty_page(sponsored):
    client = _client([{"sponsoredLegislation": [], "pagination": {"next": "..."}}])
    assert list(client.paginate("/member/X/sponsored-legislation", "sponsoredLegislation")) == []
    assert client.session.get.call_count == 1


def test_throttling_is_retried_and_honours_retry_after(sponsored):
    slept: list[float] = []
    session = MagicMock()
    session.get.side_effect = [
        _response({}, status_code=429, headers={"Retry-After": "9"}),
        _response(sponsored),
    ]
    client = CongressAPIClient(API_KEY, session=session, sleeper=slept.append)

    client.get("/member/P000197/sponsored-legislation")
    assert slept == [9]


def test_max_requests_stops_cleanly(sponsored):
    client = _client([sponsored] * 5, max_requests=1)
    client.get("/x")
    with pytest.raises(RequestBudgetExhausted):
        client.get("/x")


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


def test_ingest_stores_bills_and_marks_the_sponsor(db_session, sponsored, cosponsored):
    member = _member(db_session)
    client = _client([sponsored, cosponsored])

    result = ingest_member_bills(db_session, API_KEY, client=client)

    bills = db_session.query(Bill).all()
    assert len(bills) == len(sponsored["sponsoredLegislation"]) + len(
        cosponsored["cosponsoredLegislation"]
    )
    assert result["sponsorships"] == len(sponsored["sponsoredLegislation"])
    assert result["cosponsorships"] == len(cosponsored["cosponsoredLegislation"])
    assert {s.member_id for s in db_session.query(BillSponsorship).all()} == {member.id}


def test_sponsor_and_cosponsor_are_stored_as_different_acts(db_session, sponsored, cosponsored):
    # Measured on the live API, one member sponsored 71 bills and cosponsored
    # 1,562. Collapsing the two would make any detector built on them fire on
    # everybody.
    _member(db_session)
    ingest_member_bills(db_session, API_KEY, client=_client([sponsored, cosponsored]))

    sponsored_rows = db_session.query(BillSponsorship).filter_by(is_sponsor=True).count()
    cosponsored_rows = db_session.query(BillSponsorship).filter_by(is_sponsor=False).count()
    assert sponsored_rows == len(sponsored["sponsoredLegislation"])
    assert cosponsored_rows == len(cosponsored["cosponsoredLegislation"])


def test_sponsored_only_skips_the_cosponsorship_sweep(db_session, sponsored):
    _member(db_session)
    client = _client([sponsored])
    ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

    assert db_session.query(BillSponsorship).filter_by(is_sponsor=False).count() == 0
    assert client.session.get.call_count == 1


def test_bills_without_a_policy_area_are_stored_and_counted(db_session, sponsored, cosponsored):
    # CRS did not classify bills systematically until around the 111th Congress.
    # Dropping those rows would mean re-fetching them when a policy area lands.
    _member(db_session)
    result = ingest_member_bills(db_session, API_KEY, client=_client([sponsored, cosponsored]))

    unclassified = db_session.query(Bill).filter(Bill.policy_area.is_(None)).count()
    assert unclassified > 0
    assert result["bills_without_policy_area"] == unclassified


def test_only_sector_mapped_policy_areas_are_counted_as_mappable(
    db_session, sponsored, cosponsored
):
    _member(db_session)
    result = ingest_member_bills(db_session, API_KEY, client=_client([sponsored, cosponsored]))

    # The fixture carries Health (mapped) and Taxation (deliberately not).
    assert result["bills_mapped_to_a_sector"] > 0
    assert result["bills_mapped_to_a_sector"] < result["bills"]


def test_a_null_policy_area_never_overwrites_a_known_one(db_session, sponsored):
    # The same bill arrives once per member who touched it, and a thinner
    # response must not erase what a richer one supplied.
    _member(db_session)
    ingest_member_bills(db_session, API_KEY, client=_client([sponsored]), include_cosponsored=False)
    classified = db_session.query(Bill).filter(Bill.policy_area.isnot(None)).first()
    assert classified is not None
    known = classified.policy_area

    stripped = {
        "sponsoredLegislation": [
            {**b, "policyArea": None} for b in sponsored["sponsoredLegislation"]
        ],
        "pagination": {},
    }
    ingest_member_bills(db_session, API_KEY, client=_client([stripped]), include_cosponsored=False)

    db_session.refresh(classified)
    assert classified.policy_area == known


def test_rerunning_adds_no_duplicates(db_session, sponsored, cosponsored):
    _member(db_session)
    for _ in range(2):
        result = ingest_member_bills(db_session, API_KEY, client=_client([sponsored, cosponsored]))
    assert result["sponsorships"] == 0
    assert result["cosponsorships"] == 0
    assert db_session.query(Bill).count() == len(sponsored["sponsoredLegislation"]) + len(
        cosponsored["cosponsoredLegislation"]
    )


def test_ingest_without_a_roster_reports_rather_than_crashing(db_session):
    result = ingest_member_bills(db_session, API_KEY, client=_client([]))
    assert result["members_queried"] == 0
    assert result["bills"] == 0


# --------------------------------------------------------------------------
# Committee referrals
# --------------------------------------------------------------------------


def _one_bill(db_session) -> Bill:
    bill = Bill(congress=118, bill_type="hr", number="1", policy_area="Energy")
    db_session.add(bill)
    db_session.commit()
    return bill


def test_referrals_store_full_committees_and_subcommittees(db_session, committees_hr1):
    bill = _one_bill(db_session)
    client = _client([committees_hr1])

    result = fetch_bill_committees(db_session, API_KEY, bills=[bill], client=client)

    ids = {row.committee_id for row in db_session.query(BillCommittee).all()}
    assert "HSIF" in ids  # Energy and Commerce
    assert "HSIF03" in ids  # its Energy Subcommittee, referred ten days later
    assert result["referrals"] == db_session.query(BillCommittee).count()


def test_referral_dates_are_stored(db_session, committees_hr4644):
    # The date is what makes this a dated event rather than a standing overlap,
    # which is the whole difference from committee_jurisdiction_conflict.
    bill = _one_bill(db_session)
    fetch_bill_committees(db_session, API_KEY, bills=[bill], client=_client([committees_hr4644]))

    row = db_session.query(BillCommittee).one()
    assert row.committee_id == "HSWM"
    assert isinstance(row.activity_date, datetime)


def test_a_looked_up_bill_is_not_looked_up_again(db_session, committees_hr4644):
    # One request per bill against tens of thousands of bills, so "no
    # committees" and "not looked up yet" have to stay distinguishable.
    bill = _one_bill(db_session)
    fetch_bill_committees(db_session, API_KEY, bills=[bill], client=_client([committees_hr4644]))
    assert bill.committees_fetched is True

    second = _client([committees_hr4644])
    result = fetch_bill_committees(db_session, API_KEY, bills=[bill], client=second)
    assert result["bills_looked_up"] == 0
    assert second.session.get.call_count == 0


class TestOneUnknownMemberDoesNotAbortTheIngest:
    """Congress.gov 404s a bioguide id it does not carry, and this roster holds
    every member in history. One such id ended the sponsorship ingest for all
    12,770:

        404 Client Error: Not Found for url:
        https://api.congress.gov/v3/member/M000633/sponsored-legislation

    Two detectors -- sponsorship_conflict and bill_jurisdiction_conflict -- had
    no data at all as a result, and the step is continue-on-error so the run
    reported success.
    """

    def _roster(self, db, bioguide_ids):
        from src.db.models import Chamber, Member, Party

        for i, bioguide in enumerate(bioguide_ids):
            db.add(
                Member(
                    bioguide_id=bioguide,
                    first_name=f"First{i}",
                    last_name=f"Last{i}",
                    chamber=Chamber.HOUSE,
                    party=Party.DEMOCRAT,
                    state="CA",
                )
            )
        db.commit()

    class _Client:
        """Paginates normally, except for the ids that 404."""

        def __init__(self, missing):
            self.missing = set(missing)
            self.requests_made = 0
            self.asked = []

        def paginate(self, path, key):
            import requests

            self.requests_made += 1
            bioguide = path.split("/")[2]
            self.asked.append(bioguide)
            if bioguide in self.missing:
                response = requests.Response()
                response.status_code = 404
                raise requests.HTTPError("404 Client Error: Not Found", response=response)
            return iter(())

    def test_the_members_after_it_are_still_queried(self, db_session):
        from src.ingestion.bills import ingest_member_bills

        self._roster(db_session, ["A000001", "M000633", "C000003"])
        client = self._Client(missing={"M000633"})

        result = ingest_member_bills(db_session, "key", client=client, include_cosponsored=False)

        assert result["members_queried"] == 3
        assert "C000003" in client.asked, (
            "the member after the unknown one was never queried — one 404 still ends the ingest"
        )

    def test_a_non_404_still_propagates(self, db_session):
        """A 403 on a bad key, or a 429, must not be quietly read as 'this
        member has no bills'."""
        import requests

        from src.ingestion.bills import ingest_member_bills

        self._roster(db_session, ["A000001"])

        class Forbidden(self._Client):
            def paginate(self, path, key):
                response = requests.Response()
                response.status_code = 403
                raise requests.HTTPError("403 Forbidden", response=response)

        with pytest.raises(requests.HTTPError):
            ingest_member_bills(
                db_session, "key", client=Forbidden(missing=set()), include_cosponsored=False
            )

    def test_the_skipped_members_are_reported(self, db_session, caplog):
        """Silence here would make a roster drifting away from Congress.gov's
        ids look exactly like Congress passing no legislation."""
        import logging

        from src.ingestion.bills import ingest_member_bills

        self._roster(db_session, ["A000001", "M000633"])

        with caplog.at_level(logging.WARNING, logger="src.ingestion.bills"):
            ingest_member_bills(
                db_session,
                "key",
                client=self._Client(missing={"M000633"}),
                include_cosponsored=False,
            )

        assert any("no record for" in r.getMessage() for r in caplog.records), (
            "members Congress.gov could not resolve were skipped without saying so"
        )


# --------------------------------------------------------------------------
# Who is worth a request
# --------------------------------------------------------------------------


class TestTheRosterIsScopedToMembersAFindingCanBeAbout:
    """One Congress.gov request per member, over every member in history.

    Measured against production: the roster holds **12,770** members, of whom
    **540** are in office and **407** have a disclosure on file -- 50 of those
    being former members. The set worth asking about is the union, **590**.
    Congress.gov allows 5,000 requests an hour, so the unscoped sweep is two and
    a half hours of a 350-minute job, almost all of it spent on people who left
    Congress decades ago.

    Nothing is lost. `detect_sponsorship_conflicts` skips a sponsor with no
    transactions, and a member who has filed nothing has none;
    `detect_bill_jurisdiction_conflicts` needs the member to sit on the
    committee a bill reached, which a former member does not. This is the
    scoping `tests/test_analysis_scope.py` applies to the analyzers, with the
    same argument: the members dropped are exactly the ones producing nothing.
    """

    def _former(self, db, bioguide: str) -> Member:
        member = _member(db, bioguide=bioguide)
        member.in_office = False
        db.commit()
        return member

    def _with_a_filing(self, db, member: Member) -> Member:
        db.add(
            Disclosure(
                member_id=member.id,
                filing_year=2024,
                filing_type="PTR",
                filing_date=datetime(2024, 5, 1),
                document_id=f"DOC_{member.bioguide_id}",
                is_ptr=True,
            )
        )
        db.commit()
        return member

    def test_a_member_in_office_is_asked_about(self, db_session, sponsored, cosponsored):
        _member(db_session, bioguide="A000001")
        client = _client([sponsored, cosponsored])

        result = ingest_member_bills(db_session, API_KEY, client=client)

        assert result["members_queried"] == 1

    def test_a_former_member_who_never_filed_costs_nothing(self, db_session):
        self._former(db_session, "B000002")
        client = _client([])

        result = ingest_member_bills(db_session, API_KEY, client=client)

        assert result["members_queried"] == 0
        assert client.requests_made == 0

    def test_a_former_member_whose_filings_are_here_is_still_asked_about(
        self, db_session, sponsored, cosponsored
    ):
        # 50 of the 407 members with filings have left. Their 2024 trades are in
        # this database and a 2024 bill they sponsored is a real conflict.
        member = self._former(db_session, "C000003")
        self._with_a_filing(db_session, member)
        client = _client([sponsored, cosponsored])

        result = ingest_member_bills(db_session, API_KEY, client=client)

        assert result["members_queried"] == 1

    def test_the_historical_roster_does_not_set_the_cost(self, db_session, sponsored, cosponsored):
        _member(db_session, bioguide="D000004")
        for i in range(40):
            self._former(db_session, f"H{i:06d}")
        client = _client([sponsored, cosponsored])

        result = ingest_member_bills(db_session, API_KEY, client=client)

        assert result["members_queried"] == 1, (
            "the 40 former members with no filings were asked about; that is "
            "12,180 wasted requests at production scale"
        )

    def test_naming_members_explicitly_overrides_the_scope(
        self, db_session, sponsored, cosponsored
    ):
        # `--bioguide` is how someone re-runs one member on purpose, including a
        # former one. The scope must not quietly refuse them.
        member = self._former(db_session, "E000005")
        client = _client([sponsored, cosponsored])

        result = ingest_member_bills(
            db_session, API_KEY, client=client, bioguide_ids=[member.bioguide_id]
        )

        assert result["members_queried"] == 1
