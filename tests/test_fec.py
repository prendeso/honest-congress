"""FEC campaign-donation ingestion, pinned to real API responses.

Fixtures in `tests/fixtures/fec/` are captured verbatim from api.open.fec.gov.
Each one encodes something measured rather than assumed:

* `legislators_slice.yaml` -- `id.fec` is present for 537 of 539 sitting
  legislators, so the FEC-to-member join needs no name matching. Two synthetic
  rows are appended: a legislator with no FEC id at all, and one whose bioguide
  is not in the database. Both must be skipped, not guessed at.
* `principal_committees.json` -- Gillibrand's committee lists TWO candidate ids
  (she moved from the House to the Senate), which is why the map is keyed by
  committee rather than by candidate.
* `corporate_pacs.json` -- six real PACs covering all three outcomes: four
  resolve to a ticker, the American Peanut Shellers Association resolves to
  nothing (it is a trade association, correctly skipped), and Bechtel is
  privately held and recorded with an empty ticker so nobody guesses one.
* `boeing_pac_receipts.json` -- two real pages of Schedule A. They contain a
  refund (-$5,000) carrying memo_code "X", and two separate $5,000 receipts to
  the same committee on the same date (primary and general). The second case is
  why donations deduplicate on FEC's `sub_id` and not on the natural key: that
  key merges two real donations into one.

The request budget is the other thing under test. api.data.gov allows 1,000
requests/hour, one PAC costs ~16 requests, and a full cycle over a realistic
ticker universe runs into the thousands -- so a capped run must stop cleanly,
commit what it has, and not re-pay for the same PACs next time.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml
from sqlalchemy.exc import OperationalError

from src.db.models import CampaignDonation, Chamber, Disclosure, Member, Party, Transaction
from src.db.models import TransactionType as TT
from src.ingestion._helpers import traded_tickers
from src.ingestion.fec import (
    FECClient,
    RequestBudgetExhausted,
    corporate_pacs,
    ingest_campaign_donations,
    member_index_by_fec_id,
    principal_committees,
)
from src.ingestion.rate_limit import RateLimiter
from src.ingestion.sec_tickers import TickerResolver

FIXTURES = Path(__file__).parent / "fixtures"
FEC = FIXTURES / "fec"
SEC = FIXTURES / "sec" / "company_tickers.json"

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


@pytest.fixture
def resolver() -> TickerResolver:
    session = MagicMock()
    session.get.return_value = _response(json.loads(SEC.read_text()))
    return TickerResolver(session=session)


@pytest.fixture
def receipts() -> dict:
    return json.loads((FEC / "boeing_pac_receipts.json").read_text())


def _client(responses, **kwargs) -> FECClient:
    """A client whose HTTP layer replays a fixed list of payloads."""
    session = MagicMock()
    session.get.side_effect = [_response(p) for p in responses]
    kwargs.setdefault("sleeper", lambda _: None)
    return FECClient(API_KEY, session=session, **kwargs)


# --------------------------------------------------------------------------
# Client: budget, throttling, pagination
# --------------------------------------------------------------------------


def test_client_requires_a_key():
    with pytest.raises(ValueError, match="FEC_API_KEY"):
        FECClient("")


def test_key_is_sent_but_never_in_the_path():
    client = _client([{"results": []}])
    client.get("/committees/", {"designation": "P"})
    args, kwargs = client.session.get.call_args
    assert "api_key" not in args[0]
    assert kwargs["params"]["api_key"] == API_KEY


def test_max_requests_stops_the_run_cleanly():
    client = _client([{"results": []}] * 5, max_requests=2)
    client.get("/committees/", {})
    client.get("/committees/", {})
    with pytest.raises(RequestBudgetExhausted):
        client.get("/committees/", {})
    assert client.requests_made == 2


def test_throttling_is_retried_and_honours_retry_after():
    slept: list[float] = []
    session = MagicMock()
    session.get.side_effect = [
        _response({}, status_code=429, headers={"Retry-After": "7"}),
        _response({}, status_code=429, headers={"Retry-After": "11"}),
        _response({"results": [{"ok": True}]}),
    ]
    client = FECClient(API_KEY, session=session, sleeper=slept.append)

    assert client.get("/committees/", {})["results"] == [{"ok": True}]
    assert slept == [7, 11]
    assert client.requests_made == 3


def test_throttling_without_retry_after_uses_the_backoff_table():
    slept: list[float] = []
    session = MagicMock()
    session.get.side_effect = [
        _response({}, status_code=429),
        _response({}, status_code=429),
        _response({"results": []}),
    ]
    client = FECClient(
        API_KEY, session=session, sleeper=slept.append, backoff_seconds=(2, 4, 8, 16)
    )

    client.get("/committees/", {})
    assert slept == [2, 4]


def test_persistent_throttling_eventually_gives_up():
    session = MagicMock()
    session.get.side_effect = [_response({}, status_code=429)] * 8
    client = FECClient(API_KEY, session=session, sleeper=lambda _: None)

    with pytest.raises(requests.exceptions.RetryError):
        client.get("/committees/", {})


def test_rate_limiter_allows_a_burst_then_waits():
    # The quota is per hour, not a rate: 200 requests should not be paced out
    # over 13 minutes just because the hourly limit is 900.
    now = [0.0]
    slept: list[float] = []
    limiter = RateLimiter(3, 3600, clock=lambda: now[0], sleeper=slept.append)

    for _ in range(3):
        limiter.acquire()
    assert slept == []

    limiter.acquire()
    assert slept and slept[0] == pytest.approx(3600)


def test_rate_limiter_forgets_requests_older_than_an_hour():
    now = [0.0]
    slept: list[float] = []
    limiter = RateLimiter(2, 3600, clock=lambda: now[0], sleeper=slept.append)

    limiter.acquire()
    limiter.acquire()
    now[0] = 3601
    limiter.acquire()
    assert slept == []


def test_schedule_pagination_echoes_last_indexes_not_a_page_number(receipts):
    # The schedule endpoints ignore `page` and return the first page forever.
    client = _client([receipts["page1"], receipts["page2"]])
    rows = list(client.paginate_keyset("/schedules/schedule_a/", {"contributor_id": "C00142711"}))

    assert len(rows) == len(receipts["page1"]["results"]) + len(receipts["page2"]["results"])
    second_call_params = client.session.get.call_args_list[1][1]["params"]
    assert "page" not in second_call_params
    assert (
        second_call_params["last_index"]
        == receipts["page1"]["pagination"]["last_indexes"]["last_index"]
    )


# --------------------------------------------------------------------------
# Mapping stages
# --------------------------------------------------------------------------


def _member(db, bioguide: str, chamber=Chamber.SENATE) -> Member:
    member = Member(
        bioguide_id=bioguide,
        first_name="Test",
        last_name=bioguide,
        chamber=chamber,
        party=Party.DEMOCRAT,
        state="WA",
    )
    db.add(member)
    db.commit()
    return member


def test_member_index_maps_every_fec_id_including_a_chamber_switch(db_session):
    cantwell = _member(db_session, "C000127")
    gillibrand = _member(db_session, "G000555")
    davids = _member(db_session, "D000629", Chamber.HOUSE)

    legislators = yaml.safe_load((FEC / "legislators_slice.yaml").read_text())
    with patch("src.ingestion.fec._fetch_yaml", return_value=legislators):
        index = member_index_by_fec_id(db_session)

    # Both of Cantwell's and Gillibrand's candidate ids resolve to one member.
    assert index["S8WA00194"] == cantwell.id
    assert index["H2WA01054"] == cantwell.id
    assert index["S0NY00410"] == gillibrand.id
    assert index["H6NY20167"] == gillibrand.id
    assert index["H8KS03155"] == davids.id


def test_member_index_skips_legislators_it_cannot_join(db_session):
    _member(db_session, "C000127")
    legislators = yaml.safe_load((FEC / "legislators_slice.yaml").read_text())
    with patch("src.ingestion.fec._fetch_yaml", return_value=legislators):
        index = member_index_by_fec_id(db_session)

    # Z999999 has no FEC id; X000001 is not in the database. Neither may be
    # guessed into someone else's row.
    assert "H0XX00001" not in index
    assert set(index.values()) == {db_session.query(Member).one().id}


def test_principal_committees_sends_candidate_ids_in_batches():
    payload = json.loads((FEC / "principal_committees.json").read_text())
    client = _client([payload])
    mapping = principal_committees(client, ["S8WA00194", "S0NY00410", "H8KS03155"])

    assert mapping["C00349506"] == "S8WA00194"
    assert mapping["C00670034"] == "H8KS03155"
    # Gillibrand's committee lists both her House and Senate candidate ids.
    assert mapping["C00413914"] in {"S0NY00410", "H6NY20167"}
    assert client.session.get.call_count == 1


def test_principal_committees_asks_only_for_principal_committees():
    client = _client([json.loads((FEC / "principal_committees.json").read_text())])
    principal_committees(client, ["S8WA00194"])
    assert client.session.get.call_args[1]["params"]["designation"] == "P"


def test_corporate_pacs_resolve_and_skip_correctly(resolver):
    client = _client([json.loads((FEC / "corporate_pacs.json").read_text())])
    pacs = corporate_pacs(client, resolver, 2024)

    tickers = {t for t, _ in pacs.values()}
    assert {"BA", "LMT", "CAT", "MMM"} <= tickers
    # A trade association and a privately held firm: both correctly absent.
    names = {name for _, name in pacs.values()}
    assert not any("PEANUT" in n.upper() for n in names)
    assert not any("BECHTEL" in n.upper() for n in names)


def test_corporate_pacs_narrow_to_traded_tickers(resolver):
    # This is what keeps the run affordable: a donation in a company no member
    # holds cannot produce a finding, so it is not worth a request.
    client = _client([json.loads((FEC / "corporate_pacs.json").read_text())])
    pacs = corporate_pacs(client, resolver, 2024, restrict_to={"BA"})

    assert {t for t, _ in pacs.values()} == {"BA"}


def test_traded_tickers_reads_what_members_actually_hold(db_session):
    member = _member(db_session, "T000001")
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 5, 1),
        document_id=f"DOC_{member.id}",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(disclosure)
    db_session.commit()
    for ticker in ("ba", " LMT ", None):
        db_session.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=datetime(2024, 4, 1),
                transaction_type=TT.PURCHASE,
                description="x",
                ticker=ticker,
            )
        )
    db_session.commit()

    assert traded_tickers(db_session) == {"BA", "LMT"}


# --------------------------------------------------------------------------
# End-to-end ingestion
# --------------------------------------------------------------------------


def _seed_for_ingest(db_session):
    member = _member(db_session, "D000629", Chamber.HOUSE)
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 5, 1),
        document_id=f"DOC_{member.id}",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(disclosure)
    db_session.commit()
    db_session.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 4, 1),
            transaction_type=TT.PURCHASE,
            description="Boeing Co",
            ticker="BA",
        )
    )
    db_session.commit()
    return member


def _ingest(db_session, resolver, receipts, **kwargs):
    legislators = yaml.safe_load((FEC / "legislators_slice.yaml").read_text())
    committees = json.loads((FEC / "principal_committees.json").read_text())
    pacs = json.loads((FEC / "corporate_pacs.json").read_text())
    client = _client([committees, pacs, receipts["page1"], receipts["page2"]], **kwargs)
    with patch("src.ingestion.fec._fetch_yaml", return_value=legislators):
        return ingest_campaign_donations(
            db_session, API_KEY, 2024, resolver=resolver, client=client
        )


def test_ingest_stores_donations_attributed_to_the_right_member(db_session, resolver, receipts):
    member = _seed_for_ingest(db_session)
    result = _ingest(db_session, resolver, receipts)

    rows = db_session.query(CampaignDonation).all()
    assert rows, "the fixture contains a receipt to Sharice Davids' committee"
    assert {r.member_id for r in rows} == {member.id}
    assert {r.ticker for r in rows} == {"BA"}
    assert {r.source for r in rows} == {"fec"}
    assert result["imported"] == len(rows)


def test_ingest_skips_memo_entries_and_refunds(db_session, resolver, receipts):
    _seed_for_ingest(db_session)
    _ingest(db_session, resolver, receipts)

    amounts = [r.amount for r in db_session.query(CampaignDonation).all()]
    # The fixture's only memo row is a -$5,000 refund. Counting it as a
    # donation would be wrong twice over.
    assert all(a > 0 for a in amounts)


def test_ingest_stores_the_fec_transaction_id(db_session, resolver, receipts):
    _seed_for_ingest(db_session)
    _ingest(db_session, resolver, receipts)

    rows = db_session.query(CampaignDonation).all()
    assert all(r.external_id for r in rows)
    assert len({r.external_id for r in rows}) == len(rows)


def test_receipts_to_committees_with_no_sitting_member_are_counted_not_dropped(
    db_session, resolver, receipts
):
    _seed_for_ingest(db_session)
    result = _ingest(db_session, resolver, receipts)

    # Most of the fixture's receipts go to committees outside the three in
    # principal_committees.json. Reporting that count is what distinguishes
    # "nothing matched" from "nothing ran".
    assert result["skipped_unmapped_recipient"] > 0


def test_rerunning_imports_nothing_new(db_session, resolver, receipts):
    _seed_for_ingest(db_session)
    first = _ingest(db_session, resolver, receipts)
    second = _ingest(db_session, resolver, receipts)

    assert first["imported"] > 0
    # The second run skips the PAC entirely rather than re-paying for it.
    assert second["imported"] == 0
    assert second["pacs_already_ingested"] >= 1


def test_resume_false_rescans_a_pac_already_stored(db_session, resolver, receipts):
    _seed_for_ingest(db_session)
    _ingest(db_session, resolver, receipts)

    legislators = yaml.safe_load((FEC / "legislators_slice.yaml").read_text())
    committees = json.loads((FEC / "principal_committees.json").read_text())
    pacs = json.loads((FEC / "corporate_pacs.json").read_text())
    client = _client([committees, pacs, receipts["page1"], receipts["page2"]])
    with patch("src.ingestion.fec._fetch_yaml", return_value=legislators):
        again = ingest_campaign_donations(
            db_session, API_KEY, 2024, resolver=resolver, client=client, resume=False
        )

    assert again["pacs_already_ingested"] == 0
    assert again["imported"] == 0  # rescanned, but every row is already stored
    assert again["duplicates"] > 0


def test_ingest_without_a_roster_reports_rather_than_crashing(db_session, resolver, receipts):
    with patch("src.ingestion.fec._fetch_yaml", return_value=[]):
        result = ingest_campaign_donations(
            db_session, API_KEY, 2024, resolver=resolver, client=_client([])
        )
    assert result["imported"] == 0
    assert result["pacs_queried"] == 0


def test_a_capped_run_commits_what_it_managed_and_says_so(db_session, resolver, receipts):
    _seed_for_ingest(db_session)
    result = _ingest(db_session, resolver, receipts, max_requests=3)

    assert result["stopped_early"] is True
    assert result["requests_made"] == 3


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def _count_selects(engine, callable_):
    from sqlalchemy import event

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        callable_()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


class TestCostDoesNotTrackTheNumberOfReceipts:
    """One SELECT per incoming receipt, to learn whether it was already stored.

    The last production run imported **32,515** donations. That was 32,515
    network round trips to a database on another host, and on a rerun every one
    is already present -- the whole cost buys the answer "nothing to do".

    Same fix as `src/ingestion/usaspending.py` and `lda.py`: read the sub_ids
    already stored for this source once, and use that set as the in-batch guard
    too, since autoflush=False hides a row added earlier in the loop from a
    query. A receipt with no sub_id still falls back to a single query, so the
    old NULL semantics are preserved exactly rather than quietly redefined.
    """

    # Sharice Davids' principal committee, the one `_seed_for_ingest` creates a
    # member for. Cloning any other receipt gives a donation with no sitting
    # member, which is correctly counted and not stored -- and would make this
    # measure nothing.
    SEEDED_COMMITTEE = "C00670034"

    def _receipts(self, n: int, receipts: dict) -> dict:
        one = dict(receipts["page1"]["results"][0], committee_id=self.SEEDED_COMMITTEE)
        return {
            "page1": {
                "results": [dict(one, sub_id=f"sub-{i:05d}") for i in range(n)],
                "pagination": {"pages": 1, "page": 1},
            },
            "page2": {"results": [], "pagination": {"pages": 1, "page": 1}},
        }

    def test_one_receipt_and_forty_cost_the_same_to_look_up(
        self, db_session, engine, resolver, receipts
    ):
        _seed_for_ingest(db_session)

        one = _count_selects(
            engine, lambda: _ingest(db_session, resolver, self._receipts(1, receipts))
        )
        forty = _count_selects(
            engine, lambda: _ingest(db_session, resolver, self._receipts(40, receipts))
        )

        assert len(one) == len(forty), (
            f"lookups grew with the number of receipts: {len(one)} -> {len(forty)}"
        )

    def test_the_donations_are_still_stored_once_each(self, db_session, resolver, receipts):
        _seed_for_ingest(db_session)
        payload = self._receipts(12, receipts)

        first = _ingest(db_session, resolver, payload)
        second = _ingest(db_session, resolver, payload)

        assert first["imported"] == 12
        assert second["imported"] == 0
        assert db_session.query(CampaignDonation).count() == 12


class TestADroppedDatabaseConnectionDoesNotEndTheRun:
    """Two caches here, not one, and both go stale on a rollback.

    `seen_sub_ids` holds the FEC transaction ids already stored. `already_done`
    holds the tickers that already carry donations for this cycle, and exists so
    a resumed run does not re-spend requests on PACs it has finished. A rollback
    undoes donations, so afterwards the first would skip re-importing them and
    the second would claim the PAC was done when its rows are gone.
    """

    def _dropped(self) -> OperationalError:
        exc = OperationalError("COMMIT", {}, Exception("SSL error: unexpected eof"))
        exc.connection_invalidated = True
        return exc

    def _flaky_commit(self, db_session, fail_on):
        real = db_session.commit
        calls = {"n": 0}

        def commit():
            calls["n"] += 1
            if calls["n"] in fail_on:
                raise self._dropped()
            return real()

        return commit

    def test_the_donations_survive_a_dropped_connection(self, db_session, resolver, receipts):
        _seed_for_ingest(db_session)
        with patch.object(db_session, "commit", side_effect=self._flaky_commit(db_session, {1})):
            result = _ingest(db_session, resolver, receipts)

        assert result["connection_losses"] >= 1
        assert result["pacs_lost_to_the_database"] == []
        assert db_session.query(CampaignDonation).count() == result["imported"] > 0

    def test_the_rows_lost_to_the_rollback_are_re_imported(self, db_session, resolver, receipts):
        """Fails if `seen_sub_ids` is not rebuilt: the retry skips every row."""
        _seed_for_ingest(db_session)
        with patch.object(db_session, "commit", side_effect=self._flaky_commit(db_session, {1})):
            result = _ingest(db_session, resolver, receipts)

        assert db_session.query(CampaignDonation).count() > 0
        assert result["duplicates"] == 0

    def test_a_pac_lost_to_repeated_drops_is_named(self, db_session, resolver, receipts):
        _seed_for_ingest(db_session)
        with patch.object(
            db_session, "commit", side_effect=self._flaky_commit(db_session, {1, 2, 3, 4})
        ):
            result = _ingest(db_session, resolver, receipts)

        assert result["pacs_lost_to_the_database"], "a lost PAC must be named, not silent"
        assert db_session.query(CampaignDonation).count() == 0

    def test_a_real_database_error_still_raises(self, db_session, resolver, receipts):
        _seed_for_ingest(db_session)

        def broken():
            raise OperationalError("COMMIT", {}, Exception("syntax error"))

        with patch.object(db_session, "commit", side_effect=broken):
            with pytest.raises(OperationalError):
                _ingest(db_session, resolver, receipts)
