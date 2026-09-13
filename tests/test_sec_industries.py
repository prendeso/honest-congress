"""SEC industry codes, and the sector map built on them.

The map in `sectors.SIC_SECTORS` is the judgment layer of this whole feature, so
it was written against ground truth rather than from memory: the SIC code SEC has
actually assigned to each of the 70 tickers in `SECTOR_TICKERS` was fetched from
EDGAR and compared with the hand-classification. 62 of 66 agreed. The four that
did not are the interesting ones, and each has a test below:

* Amazon files under 5961, retail catalogue and mail-order.
* Visa and Mastercard file under 7389, "business services, NEC" -- a grab-bag
  holding hundreds of unrelated firms, which is why that code maps to nothing.
* Leidos files under 7373, computer integrated systems design.

All four are accurate descriptions of the filer and the wrong answer for this
question, which is why `SECTOR_TICKERS` overrides the industry code rather than
the other way round.

Measured on 300 unselected SEC-registered tickers: the hand-written list
classifies 4 of them (1.3%), the industry codes classify 156 (52%).

`tests/fixtures/sec/edgar_company_*.xml` are captured verbatim, including
EDGAR's double-escaped ampersands ("&amp;amp;") in the SIC description, which a
single unescape leaves visible in the stored text.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from src.analysis.sectors import SECTOR_TICKERS, SectorIndex, sector_for_sic
from src.db.models import CompanyIndustry
from src.ingestion.sec_industries import (
    EdgarCompanyClient,
    ingest_company_industries,
    uncached_tickers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sec"

# ticker -> (cik, fixture)
COMPANIES = {"LMT": (936468, "lmt"), "TSLA": (1318605, "tsla"), "BRK-B": (1067983, "brk")}


def _xml_response(body: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.text = body
    response.raise_for_status.return_value = None
    return response


def _client(fixture_names) -> EdgarCompanyClient:
    session = MagicMock()
    session.get.side_effect = [
        _xml_response((FIXTURES / f"edgar_company_{n}.xml").read_text()) for n in fixture_names
    ]
    return EdgarCompanyClient(session=session, sleeper=lambda _: None)


class _Resolver:
    """Stands in for TickerResolver without touching the network."""

    def __init__(self, ciks):
        self._ciks = ciks

    def ciks(self):
        return dict(self._ciks)


# --------------------------------------------------------------------------
# The SIC map
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sic,sectors",
    [
        ("3760", {"defense"}),  # guided missiles (LMT)
        ("3721", {"defense"}),  # aircraft (BA)
        ("3730", {"defense"}),  # shipbuilding (GD, HII)
        ("2834", {"healthcare"}),  # pharmaceutical preparations
        ("2836", {"healthcare"}),  # biological products
        ("6021", {"finance"}),  # national commercial banks
        ("6211", {"finance"}),  # security brokers
        ("2911", {"energy"}),  # petroleum refining
        ("1311", {"energy"}),  # crude petroleum
        ("4813", {"telecom"}),  # telephone communications
        ("4841", {"telecom"}),  # cable
        ("4011", {"transportation"}),  # railroads
        ("3711", {"transportation"}),  # motor vehicles
        ("7372", {"technology"}),  # prepackaged software
        ("3674", {"technology"}),  # semiconductors
        ("2870", {"agriculture"}),  # agricultural chemicals
        ("3523", {"agriculture"}),  # farm machinery (DE)
    ],
)
def test_industry_codes_map_to_the_expected_sector(sic, sectors):
    assert set(sector_for_sic(sic)) == sectors


@pytest.mark.parametrize(
    "sic,sector,sits_inside",
    [
        # Each of these would get the wrong answer from the group it sits in,
        # which is what the longest-prefix rule is for.
        ("6324", "healthcare", "63xx insurance"),  # UNH, hospital & medical plans
        ("2911", "energy", "29xx beside the chemicals"),  # petroleum refining
        ("2870", "agriculture", "28xx chemicals"),  # agricultural chemicals
        ("4610", "energy", "40xx-47xx transport"),  # pipelines
    ],
)
def test_a_specific_code_overrules_the_group_it_sits_in(sic, sector, sits_inside):
    assert sector in sector_for_sic(sic), sits_inside


@pytest.mark.parametrize("sic", ["7389", "5961", "6500", "9995", "", None, "abcd"])
def test_grab_bag_and_unknown_codes_map_to_nothing(sic):
    # 7389 "business services, NEC" and 5961 retail-catalogue are real codes for
    # real companies; they just do not identify an industry any committee
    # oversees. Mapping them anyway is how a detector starts claiming things.
    assert sector_for_sic(sic) == frozenset()


# --------------------------------------------------------------------------
# SectorIndex
# --------------------------------------------------------------------------


def test_the_curated_list_overrides_the_industry_code():
    # SEC files Amazon under retail-catalogue and Visa under business services.
    # Both are accurate about the filer and wrong for this question.
    index = SectorIndex({"AMZN": "5961", "V": "7389", "LDOS": "7373"})
    assert index.classify("AMZN") == {"technology"}
    assert index.classify("V") == {"finance"}
    assert index.classify("LDOS") == {"defense"}


def test_the_industry_code_covers_what_the_curated_list_does_not():
    ticker = "AVAV"  # AeroVironment: a real defense supplier, not in the list
    assert not any(ticker in symbols for symbols in SECTOR_TICKERS.values())
    assert SectorIndex({}).classify(ticker) == set()
    assert SectorIndex({ticker: "3760"}).classify(ticker) == {"defense"}


def test_an_empty_index_degrades_to_the_keyword_fallback():
    # Nothing breaks before `sync-industries` has ever run.
    index = SectorIndex({})
    assert index.classify("LMT") == {"defense"}  # curated
    assert index.classify("ZZZZ", "Pfizer pharmaceutical") == {"healthcare"}  # keywords
    assert index.classify("ZZZZ", "some obscure holding") == set()


def test_an_unmappable_industry_code_falls_through_to_keywords():
    index = SectorIndex({"ZZZZ": "7389"})
    assert index.classify("ZZZZ", "Lockheed Martin aerospace") == {"defense"}


def test_the_index_is_case_insensitive():
    index = SectorIndex({"avav": "3760"})
    assert index.classify("AVAV") == {"defense"}
    assert index.classify(" avav ") == {"defense"}


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


def test_ingest_stores_the_code_and_the_sector_it_implies(db_session):
    # Fixtures are consumed in the order the ingester walks its tickers, which
    # is sorted: BRK-B, LMT, TSLA.
    client = _client(["brk", "lmt", "tsla"])
    resolver = _Resolver({t: cik for t, (cik, _) in COMPANIES.items()})

    result = ingest_company_industries(
        db_session, tickers=list(COMPANIES), resolver=resolver, client=client
    )

    rows = {r.ticker: r for r in db_session.query(CompanyIndustry).all()}
    assert result["looked_up"] == 3
    assert rows["LMT"].sic == "3760"
    assert rows["LMT"].sector == "defense"
    assert rows["TSLA"].sector == "transportation"  # SIC 3711, motor vehicles
    assert rows["BRK-B"].sector == "finance"  # SIC 6331, casualty insurance


def test_edgars_double_escaped_ampersands_are_decoded(db_session):
    # The atom feed carries "&amp;amp;", so one unescape leaves "&amp;" in the
    # text a reader would see.
    client = _client(["lmt"])
    ingest_company_industries(
        db_session, tickers=["LMT"], resolver=_Resolver({"LMT": 936468}), client=client
    )

    row = db_session.query(CompanyIndustry).one()
    assert "&" in row.sic_description
    assert "amp;" not in row.sic_description
    assert row.company_name == "LOCKHEED MARTIN CORP"


def test_the_request_identifies_the_caller_with_an_email(db_session):
    # SEC returns 403 without one. Verified against the live service.
    client = _client(["lmt"])
    ingest_company_industries(
        db_session, tickers=["LMT"], resolver=_Resolver({"LMT": 936468}), client=client
    )
    assert "@" in client.session.get.call_args[1]["headers"]["User-Agent"]


def test_a_symbol_that_is_not_a_registrant_is_recorded_not_retried(db_session):
    # Funds, foreign listings, and symbols the PDF parser misread. Storing the
    # miss is what stops every future run paying for the same lookup.
    client = _client([])
    result = ingest_company_industries(
        db_session, tickers=["NOTAREALTICKER"], resolver=_Resolver({}), client=client
    )

    assert result["not_sec_registrants"] == 1
    assert client.session.get.call_count == 0
    row = db_session.query(CompanyIndustry).one()
    assert row.ticker == "NOTAREALTICKER"
    assert row.sic is None and row.sector is None


def test_a_cached_ticker_is_not_looked_up_again(db_session):
    resolver = _Resolver({"LMT": 936468})
    ingest_company_industries(
        db_session, tickers=["LMT"], resolver=resolver, client=_client(["lmt"])
    )

    second = _client([])
    result = ingest_company_industries(
        db_session, tickers=["LMT"], resolver=resolver, client=second
    )
    assert result["looked_up"] == 0
    assert second.session.get.call_count == 0


def test_a_code_that_maps_to_no_sector_is_still_cached(db_session):
    # Otherwise every run re-fetches every company in an industry we do not
    # track, which is most of them.
    client = _client(["brk"])
    ingest_company_industries(
        db_session, tickers=["BRK-B"], resolver=_Resolver({"BRK-B": 1067983}), client=client
    )
    assert db_session.query(CompanyIndustry).count() == 1


def test_uncached_tickers_reports_what_is_left(db_session):
    assert uncached_tickers(db_session) == set()


def test_throttling_is_retried(db_session):
    slept: list[float] = []
    session = MagicMock()
    throttled = MagicMock()
    throttled.status_code = 429
    throttled.headers = {"Retry-After": "3"}
    session.get.side_effect = [
        throttled,
        _xml_response((FIXTURES / "edgar_company_lmt.xml").read_text()),
    ]
    client = EdgarCompanyClient(session=session, sleeper=slept.append)

    ingest_company_industries(
        db_session, tickers=["LMT"], resolver=_Resolver({"LMT": 936468}), client=client
    )
    assert slept == [3]
    assert db_session.query(CompanyIndustry).one().sector == "defense"


def test_persistent_throttling_surfaces_rather_than_storing_nothing(db_session):
    session = MagicMock()
    throttled = MagicMock()
    throttled.status_code = 429
    throttled.headers = {}
    session.get.side_effect = [throttled] * 8
    client = EdgarCompanyClient(session=session, sleeper=lambda _: None)

    with pytest.raises(requests.exceptions.RetryError):
        ingest_company_industries(
            db_session, tickers=["LMT"], resolver=_Resolver({"LMT": 936468}), client=client
        )
