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
from unittest.mock import MagicMock, patch

import pytest
import requests
from sqlalchemy.exc import OperationalError

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


# --------------------------------------------------------------------------
# Transient gateway errors
# --------------------------------------------------------------------------


class TestATransientGatewayErrorIsRetriedNotFatal:
    """The 522 that killed a production sponsorship ingest.

        requests.exceptions.HTTPError: 522 Server Error: status code 522 for url:
        https://api.congress.gov/v3/member/D000243/sponsored-legislation

    It was raised on the FIRST attempt, because the retry loop only ever
    retried 429. `_sponsorship_pages` re-raises anything that is not a 404 by
    design -- a 403 on a bad key must never read as "this member has no bills"
    -- so one gateway hiccup ended the sweep for every member after that one,
    the CLI exited non-zero, and `continue-on-error: true` on the workflow step
    reported the run as a success. `sponsorship_conflict` and
    `bill_jurisdiction_conflict` both showed zero findings afterwards.

    522 is Cloudflare's "connection timed out to the origin". It is the most
    transient error there is: the same URL answered on the next run.
    """

    @pytest.mark.parametrize("status", [429, 502, 503, 504, 520, 521, 522, 523, 524])
    def test_it_is_retried_and_then_succeeds(self, sponsored, status):
        slept: list[float] = []
        session = MagicMock()
        session.get.side_effect = [_response({}, status_code=status), _response(sponsored)]
        client = CongressAPIClient(API_KEY, session=session, sleeper=slept.append)

        payload = client.get("/member/D000243/sponsored-legislation")

        assert payload == sponsored
        assert slept == [2], "should have backed off once before the retry"
        assert session.get.call_count == 2

    def test_the_exact_production_failure_no_longer_ends_the_sweep(self, db_session, sponsored):
        # Three members; the middle one 522s once and then answers.
        self._roster(db_session, ["A000001", "D000243", "C000003"])
        session = MagicMock()
        session.get.side_effect = [
            _response(sponsored),
            _response({}, status_code=522),
            _response(sponsored),
            _response(sponsored),
        ]
        client = CongressAPIClient(API_KEY, session=session, sleeper=lambda _: None)

        result = ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

        assert result["members_queried"] == 3
        assert not result["stopped_early"]

    def test_a_server_error_that_never_clears_still_stops(self, sponsored):
        # Four attempts, four failures: give up rather than retry forever, and
        # say which status it was rather than "still throttling".
        session = MagicMock()
        session.get.side_effect = [_response({}, status_code=522) for _ in range(8)]
        client = CongressAPIClient(API_KEY, session=session, sleeper=lambda _: None)

        with pytest.raises(requests.exceptions.RetryError, match="522"):
            client.get("/member/D000243/sponsored-legislation")

        assert session.get.call_count == 4

    def test_a_genuine_server_bug_is_not_retried(self, sponsored):
        # 500 is deliberately outside the retry set: repeating a real crash four
        # times is four times the load for the same answer.
        session = MagicMock()
        session.get.side_effect = [_response({}, status_code=500), _response(sponsored)]
        client = CongressAPIClient(API_KEY, session=session, sleeper=lambda _: None)

        with pytest.raises(requests.exceptions.HTTPError):
            client.get("/member/X/sponsored-legislation")

        assert session.get.call_count == 1

    def _roster(self, db, bioguides):
        for bioguide in bioguides:
            _member(db, bioguide=bioguide)


class TestANetworkFailureIsRetriedNotFatal:
    """The other half of the same hole, found by the reporting that found the
    first half.

    Retrying transient STATUSES fixed a 522 that killed the bill ingest. It did
    nothing for the failures that happen before a status exists, and the very
    next run showed one:

        requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='www.sec.gov',
        port=443): Read timed out. (read timeout=45)

    One slow response out of roughly a thousand, on a step whose entire job is
    to cache a value per ticker. The step died and reported success under
    `continue-on-error`, and only the new step-outcome block in the run summary
    said so.
    """

    @pytest.mark.parametrize(
        "error",
        [
            requests.exceptions.ReadTimeout("read timed out"),
            requests.exceptions.ConnectTimeout("connect timed out"),
            requests.exceptions.ConnectionError("connection reset"),
            requests.exceptions.ChunkedEncodingError("truncated"),
        ],
    )
    def test_it_is_retried_and_then_succeeds(self, sponsored, error):
        slept: list[float] = []
        session = MagicMock()
        session.get.side_effect = [error, _response(sponsored)]
        client = CongressAPIClient(API_KEY, session=session, sleeper=slept.append)

        assert client.get("/member/X/sponsored-legislation") == sponsored
        assert slept == [2]
        assert session.get.call_count == 2

    def test_a_failure_that_never_clears_gives_up_and_names_it(self, sponsored):
        session = MagicMock()
        session.get.side_effect = [
            requests.exceptions.ReadTimeout("read timed out") for _ in range(8)
        ]
        client = CongressAPIClient(API_KEY, session=session, sleeper=lambda _: None)

        with pytest.raises(requests.exceptions.RetryError, match="ReadTimeout"):
            client.get("/member/X/sponsored-legislation")

        assert session.get.call_count == 4

    def test_a_real_http_error_is_not_swallowed_as_a_network_blip(self, sponsored):
        # `raise_for_status` raising HTTPError is the server's answer, not a
        # failure to reach it, and retrying it four times would be wrong.
        session = MagicMock()
        session.get.side_effect = [_response({}, status_code=404), _response(sponsored)]
        client = CongressAPIClient(API_KEY, session=session, sleeper=lambda _: None)

        with pytest.raises(requests.exceptions.HTTPError):
            client.get("/member/X/sponsored-legislation")

        assert session.get.call_count == 1


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def _count_queries(engine, callable_):
    from sqlalchemy import event

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        callable_()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


class TestCostDoesNotTrackTheNumberOfBills:
    """Sponsorships and referrals each asked the database once per row.

    A sponsorship lookup fired once per bill per member, and Congress.gov
    returns 71 sponsored bills for a typical member; a referral lookup fired
    once per committee activity per bill. Ingestion runs as a GitHub Actions
    step writing to Railway, so each one is a network round trip.

    Both are keyed on a NATURAL key rather than an external id, because that is
    what their tables are unique on -- so the fix reads those keys once and uses
    the resulting set as the in-batch guard as well. The in-batch guard was
    always needed independently: autoflush=False hides a row added earlier in
    the loop from a query, and a committee really does repeat an activity name
    within one response.
    """

    def _sponsored(self, n: int, sponsored: dict) -> dict:
        one = sponsored["sponsoredLegislation"][0]
        return {
            "sponsoredLegislation": [
                dict(one, number=str(1000 + i), introducedDate="2024-03-01") for i in range(n)
            ],
            "pagination": {"count": n},
        }

    def test_one_bill_and_forty_cost_the_same_to_look_up(self, db_session, engine, sponsored):
        # One member throughout, so the only thing that varies is how many bills
        # come back for them.
        _member(db_session, bioguide="AA000001")

        def selects(n):
            db_session.query(BillSponsorship).delete()
            db_session.query(Bill).delete()
            db_session.commit()
            client = _client([self._sponsored(n, sponsored)])
            statements = _count_queries(
                engine,
                lambda: ingest_member_bills(
                    db_session, API_KEY, client=client, include_cosponsored=False
                ),
            )
            return [
                s
                for s in statements
                if s.lstrip().upper().startswith("SELECT") and "bill_sponsorships" in s
            ]

        one = selects(1)
        forty = selects(40)

        # Sponsorship lookups specifically. `_upsert_bill` still issues a query
        # per bill it actually has to write, which on a first pass is every one
        # of them -- see `test_a_rerun_over_stored_bills_does_not_ask_about_each_one`
        # for the case that was fixed, and note it is the re-run, not this one.
        # Asserting on the total here would claim credit for a first pass that
        # is still linear.
        assert len(one) == len(forty) == 1, (
            f"sponsorship lookups grew with the number of bills: {len(one)} -> {len(forty)}"
        )

    def test_a_rerun_over_stored_bills_does_not_ask_about_each_one(
        self, db_session, engine, sponsored
    ):
        """The nightly case. Every bill is already stored and nothing has changed.

        `_upsert_bill` costs three statements per bill -- a SELECT on the natural
        key, then the flush -- and it used to run for every payload. The same
        bill arrives once per member who touched it, and on a re-run the upsert
        reads a row and writes back exactly what was already there. Against
        Railway from a GitHub runner each of those is a network round trip.

        Asserted as "flat", not "smaller": the point is that the cost stops
        tracking the number of bills at all.
        """
        _member(db_session, bioguide="AC000001")

        def rerun_statements(n):
            db_session.query(BillSponsorship).delete()
            db_session.query(Bill).delete()
            db_session.commit()
            # First pass stores them; the pass being measured is the second.
            ingest_member_bills(
                db_session,
                API_KEY,
                client=_client([self._sponsored(n, sponsored)]),
                include_cosponsored=False,
            )
            return _count_queries(
                engine,
                lambda: ingest_member_bills(
                    db_session,
                    API_KEY,
                    client=_client([self._sponsored(n, sponsored)]),
                    include_cosponsored=False,
                ),
            )

        one = rerun_statements(1)
        forty = rerun_statements(40)

        assert len(one) == len(forty), (
            f"re-reading stored bills still costs per bill: {len(one)} -> {len(forty)}"
        )

    def test_a_payload_that_adds_a_field_still_writes_it(self, db_session, sponsored):
        """The risk the cache introduces, and the reason it is not just a key set.

        Skipping the upsert when the payload cannot change the row is only safe
        if "cannot change" is judged by the same rule the upsert applies. Get
        that wrong and a later, richer response is silently dropped -- which is
        the opposite of the null-never-overwrites rule the upsert exists to
        enforce, and it would fail quietly and permanently.
        """
        _member(db_session, bioguide="AD000001")
        thin = self._sponsored(1, sponsored)
        thin["sponsoredLegislation"][0] = {
            k: v
            for k, v in thin["sponsoredLegislation"][0].items()
            if k not in ("policyArea", "title")
        }
        ingest_member_bills(db_session, API_KEY, client=_client([thin]), include_cosponsored=False)
        stored = db_session.query(Bill).one()
        assert stored.policy_area is None

        richer = self._sponsored(1, sponsored)
        richer["sponsoredLegislation"][0]["policyArea"] = {"name": "Taxation"}
        ingest_member_bills(
            db_session, API_KEY, client=_client([richer]), include_cosponsored=False
        )
        db_session.expire_all()
        assert db_session.query(Bill).one().policy_area == "Taxation"

    def test_a_thinner_payload_still_does_not_erase_what_is_stored(self, db_session, sponsored):
        """The same rule in the other direction, now decided by the cache too."""
        _member(db_session, bioguide="AE000001")
        rich = self._sponsored(1, sponsored)
        rich["sponsoredLegislation"][0]["policyArea"] = {"name": "Health"}
        ingest_member_bills(db_session, API_KEY, client=_client([rich]), include_cosponsored=False)

        thin = self._sponsored(1, sponsored)
        thin["sponsoredLegislation"][0].pop("policyArea", None)
        ingest_member_bills(db_session, API_KEY, client=_client([thin]), include_cosponsored=False)
        db_session.expire_all()
        assert db_session.query(Bill).one().policy_area == "Health"

    def test_sponsorships_are_still_stored_once_each(self, db_session, sponsored):
        _member(db_session, bioguide="AB000001")

        for _ in range(2):
            client = _client([self._sponsored(9, sponsored)])
            ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

        assert db_session.query(BillSponsorship).count() == 9

    def test_referrals_are_still_stored_once_each(self, db_session, committees_hr1):
        member = _member(db_session, bioguide="AC000001")
        bill = Bill(
            congress=118,
            bill_type="hr",
            number="1",
            title="Test",
            introduced_date=datetime(2024, 1, 1),
        )
        db_session.add(bill)
        db_session.commit()
        assert member.id

        for _ in range(2):
            db_session.query(Bill).update({Bill.committees_fetched: False})
            db_session.commit()
            client = _client([committees_hr1])
            fetch_bill_committees(db_session, API_KEY, client=client, bills=[bill])

        rows = db_session.query(BillCommittee).all()
        keys = {(r.bill_id, r.committee_id, r.activity, r.activity_date) for r in rows}
        assert len(keys) == len(rows), "a referral was stored twice"


class TestADroppedDatabaseConnectionDoesNotEndTheRun:
    """The 06:00 cron on 2026-09-14 spent 3h54m in this loop and then died.

        psycopg.OperationalError: consuming input failed:
            SSL error: unexpected eof while reading
          File "src/ingestion/bills.py", line 321, in ingest_member_bills

    Exit code 1, the rest of the roster lost, and `continue-on-error: true` on
    the step meant it reported success. Work is committed per member, so the
    run was resumable -- a dropped packet four hours in should cost one member,
    not the remainder.

    The subtle half is the caches. `bills_by_key` and `known_sponsorships` are
    read once and added to as the loop goes. A rollback undoes rows they say
    exist, so after one they are lying: `bills_by_key` holds ids no row has any
    more, and reusing one as a foreign key attaches a sponsorship to a bill that
    is not there. They have to be rebuilt from the committed state, and that is
    what `test_a_bill_lost_to_the_rollback_is_not_referenced_by_a_stale_id`
    pins.
    """

    def _sponsored(self, sponsored: dict, numbers) -> dict:
        one = sponsored["sponsoredLegislation"][0]
        return {
            "sponsoredLegislation": [
                dict(one, number=str(n), introducedDate="2024-03-01") for n in numbers
            ],
            "pagination": {"count": len(list(numbers))},
        }

    def _dropped(self) -> OperationalError:
        """The shape SQLAlchemy hands us: `connection_invalidated` is its own flag.

        Set by the dialect's `is_disconnect`, which for psycopg means the
        connection is closed or broken -- what an SSL EOF produces.
        """
        exc = OperationalError("SELECT 1", {}, Exception("SSL error: unexpected eof"))
        exc.connection_invalidated = True
        return exc

    def test_the_run_continues_past_a_dropped_connection(self, db_session, sponsored):
        first = _member(db_session, bioguide="CA000001")
        second = _member(db_session, bioguide="CB000002")

        real_commit = db_session.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._dropped()
            return real_commit()

        # Three responses for two members: the retry re-requests the page the
        # failed attempt already fetched. A dropped connection costs one extra
        # Congress.gov request, which is the right trade against losing the
        # rest of the roster.
        client = _client(
            [
                self._sponsored(sponsored, [10]),
                self._sponsored(sponsored, [10]),
                self._sponsored(sponsored, [20]),
            ]
        )
        with patch.object(db_session, "commit", side_effect=flaky_commit):
            result = ingest_member_bills(
                db_session, API_KEY, client=client, include_cosponsored=False
            )

        assert result["connection_losses"] == 1
        assert result["members_lost_to_the_database"] == []
        assert result["members_queried"] == 2
        # Both members' sponsorships survive: the first was retried.
        assert db_session.query(BillSponsorship).count() == 2
        assert {first.id, second.id} == {
            s.member_id for s in db_session.query(BillSponsorship).all()
        }

    def test_a_bill_lost_to_the_rollback_is_not_referenced_by_a_stale_id(
        self, db_session, sponsored
    ):
        """No sponsorship may point at a bill the rollback removed.

        Without rebuilding the caches this is exactly what happens: the bill is
        gone, `bills_by_key` still has its id, and the retry writes a
        sponsorship against it.
        """
        _member(db_session, bioguide="CC000003")

        real_commit = db_session.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._dropped()
            return real_commit()

        client = _client([self._sponsored(sponsored, [31, 32, 33])] * 2)
        with patch.object(db_session, "commit", side_effect=flaky_commit):
            ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

        bill_ids = {b.id for b in db_session.query(Bill).all()}
        referenced = {s.bill_id for s in db_session.query(BillSponsorship).all()}
        assert referenced <= bill_ids, (
            f"sponsorships point at bills that do not exist: {referenced - bill_ids}"
        )
        assert db_session.query(BillSponsorship).count() == 3

    def test_a_member_lost_to_repeated_drops_is_named_not_silent(self, db_session, sponsored):
        """A silent gap is indistinguishable from a member who sponsored nothing."""
        _member(db_session, bioguide="CD000004")

        def always_dropped():
            raise self._dropped()

        client = _client([self._sponsored(sponsored, [40])] * 4)
        with patch.object(db_session, "commit", side_effect=always_dropped):
            result = ingest_member_bills(
                db_session, API_KEY, client=client, include_cosponsored=False
            )

        assert result["members_lost_to_the_database"] == ["CD000004"]
        # Three: the attempt, its one retry, and the function's closing commit,
        # which this fixture also drops. That last one is not fatal -- every
        # member is committed inside the loop, so there is nothing left for it
        # to save.
        assert result["connection_losses"] == 3

    def test_a_real_database_error_still_raises(self, db_session, sponsored):
        """Retrying a constraint violation would turn a loud bug into a silent one."""
        _member(db_session, bioguide="CE000005")

        def broken():
            raise OperationalError("SELECT 1", {}, Exception("syntax error"))

        client = _client([self._sponsored(sponsored, [50])])
        with patch.object(db_session, "commit", side_effect=broken):
            with pytest.raises(OperationalError):
                ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)


class TestTheLoopSaysWhereItHasGotTo:
    """Two production runs spent 3h54m and 3h29m here without printing a line.

    From outside, "still working" and "hung" looked identical -- which is exactly
    the state you are in when you have to decide whether to kill a run that has
    been going for four hours. One of those two did die, on a dropped
    connection, and the other was still going when its job timed out; neither
    was distinguishable from a hang while it was happening.
    """

    def _sponsored(self, sponsored: dict, n: int) -> dict:
        one = sponsored["sponsoredLegislation"][0]
        return {
            "sponsoredLegislation": [dict(one, number=str(1000 + i)) for i in range(n)],
            "pagination": {"count": n},
        }

    def test_progress_is_logged_as_the_roster_is_worked_through(
        self, db_session, sponsored, caplog
    ):
        from src.ingestion.bills import PROGRESS_EVERY_MEMBERS

        for i in range(PROGRESS_EVERY_MEMBERS + 1):
            _member(db_session, bioguide=f"PR{i:06d}")

        client = _client([self._sponsored(sponsored, 1)] * (PROGRESS_EVERY_MEMBERS + 1))
        with caplog.at_level("INFO", logger="src.ingestion.bills"):
            ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

        progress = [r for r in caplog.records if r.msg.startswith("Bills: %d/%d members")]
        assert progress, "the member loop reported nothing while it ran"

    def test_a_short_roster_does_not_log_progress_at_all(self, db_session, sponsored, caplog):
        """Nothing to report on a run that is over before anyone would look."""
        _member(db_session, bioguide="PS000001")

        client = _client([self._sponsored(sponsored, 1)])
        with caplog.at_level("INFO", logger="src.ingestion.bills"):
            ingest_member_bills(db_session, API_KEY, client=client, include_cosponsored=False)

        assert not [r for r in caplog.records if r.msg.startswith("Bills: %d/%d members")]
