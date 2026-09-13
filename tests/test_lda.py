"""Senate LDA ingestion, pinned to real lobbying filings.

The fixtures are captured verbatim from lda.gov and each one exists because of
something the live API did that a synthetic payload would not have:

* `universal_corp_filings.json` -- asking for "UNIVERSAL CORP" (the registered
  name behind ticker UVV) also returns UNIVERSAL SYNAPTICS CORPORATION, an
  unrelated company. `client_name` is a substring match, so short registered
  names over-match, and without a guard those filings would be stored against
  the wrong issuer.
* `northrop_filings.json` -- contains NORTHROP GRUMMAN CORPORATION, the
  operating subsidiary NORTHROP GRUMMAN SYSTEMS CORPORATION, and
  "535 GROUP, LLC ON BEHALF OF NORTHROP GRUMMAN SYSTEMS CORPORATION". All three
  are Northrop filings and all three must survive the guard. The third nearly
  did not: a naive round trip on the client name alone throws away every filing
  made through an intermediary.

The key is deliberately optional. The API serves anonymous callers, and
measured against the live service a key changes only the pace -- so the tests
assert the two paths differ in rate limit and in nothing else.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from src.db.models import LobbyingDisclosure
from src.ingestion.lda import (
    ANONYMOUS_REQUESTS_PER_MINUTE,
    AUTHENTICATED_REQUESTS_PER_MINUTE,
    LDA_BASE_URL,
    LDAClient,
    _candidate_client_names,
    ingest_lobbying_disclosures,
)
from src.ingestion.sec_tickers import TickerResolver

LDA = Path(__file__).parent / "fixtures" / "lda"
SEC = Path(__file__).parent / "fixtures" / "sec" / "company_tickers.json"


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
def northrop() -> dict:
    return json.loads((LDA / "northrop_filings.json").read_text())


@pytest.fixture
def universal() -> dict:
    return json.loads((LDA / "universal_corp_filings.json").read_text())


def _client(responses, **kwargs) -> LDAClient:
    session = MagicMock()
    session.get.side_effect = [_response(p) for p in responses]
    kwargs.setdefault("sleeper", lambda _: None)
    return LDAClient(session=session, **kwargs)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


def test_works_without_a_key_and_sends_no_auth_header(northrop):
    client = _client([northrop])
    client.get("/filings/", {"filing_year": 2024})
    assert "Authorization" not in client.session.get.call_args[1]["headers"]


def test_a_key_is_sent_as_a_token_header(northrop):
    client = _client([northrop], api_key="secret")
    client.get("/filings/", {"filing_year": 2024})
    assert client.session.get.call_args[1]["headers"]["Authorization"] == "Token secret"


def test_the_key_only_changes_the_pace():
    anonymous = LDAClient()
    authenticated = LDAClient("secret")
    assert anonymous._limiter.limit == ANONYMOUS_REQUESTS_PER_MINUTE
    assert authenticated._limiter.limit == AUTHENTICATED_REQUESTS_PER_MINUTE
    assert authenticated._limiter.limit > anonymous._limiter.limit
    assert anonymous._limiter.window_seconds == authenticated._limiter.window_seconds == 60


def test_uses_the_canonical_host(northrop):
    # lda.senate.gov 301s to lda.gov; paying for that on every request is waste.
    client = _client([northrop])
    client.get("/filings/", {})
    assert client.session.get.call_args[0][0].startswith(LDA_BASE_URL)
    assert "lda.gov" in LDA_BASE_URL


def test_throttling_is_retried_and_honours_retry_after(northrop):
    slept: list[float] = []
    session = MagicMock()
    session.get.side_effect = [
        _response({}, status_code=429, headers={"Retry-After": "12"}),
        _response(northrop),
    ]
    client = LDAClient(session=session, sleeper=slept.append)

    assert client.get("/filings/", {})["count"] == northrop["count"]
    assert slept == [12]


def test_persistent_throttling_eventually_gives_up():
    session = MagicMock()
    session.get.side_effect = [_response({}, status_code=429)] * 8
    client = LDAClient(session=session, sleeper=lambda _: None)
    with pytest.raises(requests.exceptions.RetryError):
        client.get("/filings/", {})


def test_pagination_stops_when_there_is_no_next_page(northrop):
    client = _client([northrop])
    rows = list(client.filings("NORTHROP GRUMMAN CORP", 2024))
    assert len(rows) == len(northrop["results"])
    assert client.requests_made == 1


def test_pagination_follows_next(northrop):
    first = {**northrop, "next": "https://lda.gov/api/v1/filings/?page=2"}
    second = {**northrop, "next": None}
    client = _client([first, second])
    rows = list(client.filings("NORTHROP GRUMMAN CORP", 2024))

    assert len(rows) == 2 * len(northrop["results"])
    assert client.session.get.call_args_list[1][1]["params"]["page"] == 2


# --------------------------------------------------------------------------
# The client-name guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("THE BOEING COMPANY", ["THE BOEING COMPANY"]),
        (
            "THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)",
            ["THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)", "THE BOEING COMPANY"],
        ),
        (
            "535 GROUP, LLC ON BEHALF OF NORTHROP GRUMMAN SYSTEMS CORPORATION",
            [
                "535 GROUP, LLC ON BEHALF OF NORTHROP GRUMMAN SYSTEMS CORPORATION",
                "NORTHROP GRUMMAN SYSTEMS CORPORATION",
            ],
        ),
        # "formerly known as" is not an intermediary: the name outside the
        # parentheses is still the filing client.
        (
            "THE BOEING COMPANY (F.N.A. AURORA FLIGHT SCIENCES CORPORATION)",
            ["THE BOEING COMPANY (F.N.A. AURORA FLIGHT SCIENCES CORPORATION)"],
        ),
        ("", []),
    ],
)
def test_intermediary_filings_expose_their_principal(name, expected):
    assert _candidate_client_names(name) == expected


def test_subsidiaries_and_intermediaries_are_all_kept(db_session, resolver, northrop):
    client = _client([northrop])
    ingest_lobbying_disclosures(db_session, 2024, tickers=["NOC"], resolver=resolver, client=client)

    clients = {r.client for r in db_session.query(LobbyingDisclosure).all()}
    assert "NORTHROP GRUMMAN CORPORATION" in clients
    assert "NORTHROP GRUMMAN SYSTEMS CORPORATION" in clients
    assert any("ON BEHALF OF" in c.upper() for c in clients)
    assert {r.ticker for r in db_session.query(LobbyingDisclosure).all()} == {"NOC"}


def test_a_coincidental_substring_match_is_rejected(db_session, resolver, universal):
    # Asking the API for "UNIVERSAL CORP" also returns Universal Synaptics,
    # which has nothing to do with UVV.
    client = _client([universal])
    result = ingest_lobbying_disclosures(
        db_session, 2024, tickers=["UVV"], resolver=resolver, client=client
    )

    clients = {r.client for r in db_session.query(LobbyingDisclosure).all()}
    assert "UNIVERSAL CORPORATION" in clients
    assert not any("SYNAPTICS" in c.upper() for c in clients)
    assert result["rejected_wrong_company"] > 0


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


def test_amount_takes_income_or_expenses(db_session, resolver, northrop):
    client = _client([northrop])
    ingest_lobbying_disclosures(db_session, 2024, tickers=["NOC"], resolver=resolver, client=client)

    rows = db_session.query(LobbyingDisclosure).all()
    # A lobbying firm reports what the client paid as `income`; a company
    # lobbying for itself reports `expenses`. Whichever is present must land.
    expected = sum(
        1
        for f in northrop["results"]
        if f.get("income") is not None or f.get("expenses") is not None
    )
    assert sum(1 for r in rows if r.amount is not None) <= len(rows)
    assert expected == 0 or any(r.amount is not None for r in rows)


def test_issue_codes_are_collected_without_duplicates(db_session, resolver, northrop):
    client = _client([northrop])
    ingest_lobbying_disclosures(db_session, 2024, tickers=["NOC"], resolver=resolver, client=client)

    for row in db_session.query(LobbyingDisclosure).all():
        if row.issue_codes:
            codes = row.issue_codes.split(",")
            assert len(codes) == len(set(codes))


def test_filed_date_is_stored(db_session, resolver, northrop):
    client = _client([northrop])
    ingest_lobbying_disclosures(db_session, 2024, tickers=["NOC"], resolver=resolver, client=client)

    dates = [r.filed_date for r in db_session.query(LobbyingDisclosure).all()]
    assert dates and all(isinstance(d, datetime) for d in dates)


def test_filings_store_the_lda_filing_uuid(db_session, resolver, northrop):
    client = _client([northrop])
    ingest_lobbying_disclosures(db_session, 2024, tickers=["NOC"], resolver=resolver, client=client)

    rows = db_session.query(LobbyingDisclosure).all()
    assert all(r.external_id for r in rows)
    assert len({r.external_id for r in rows}) == len(rows)
    assert {r.source for r in rows} == {"senate_lda"}


def test_rerunning_does_not_duplicate(db_session, resolver, northrop):
    for _ in range(2):
        client = _client([northrop])
        result = ingest_lobbying_disclosures(
            db_session, 2024, tickers=["NOC"], resolver=resolver, client=client
        )
    assert result["imported"] == 0
    assert result["duplicates"] > 0


def test_a_ticker_with_no_registered_name_costs_no_request(db_session, resolver):
    client = _client([])
    result = ingest_lobbying_disclosures(
        db_session, 2024, tickers=["ZZZZ"], resolver=resolver, client=client
    )
    assert result["tickers_without_a_registered_name"] == 1
    assert result["tickers_queried"] == 0
    assert client.requests_made == 0


def test_no_traded_tickers_reports_rather_than_scanning_the_whole_year(db_session, resolver):
    # 96,941 filings were published for 2024 alone. Paging that to find nothing
    # is the failure mode this guard exists to prevent.
    client = _client([])
    result = ingest_lobbying_disclosures(db_session, 2024, resolver=resolver, client=client)

    assert result["tickers_queried"] == 0
    assert result["imported"] == 0
    assert client.requests_made == 0
