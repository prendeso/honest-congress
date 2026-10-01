"""An amendment's posting date is when a correction was filed, not when anyone lobbied.

Three of twelve audited lobbying findings were measured against an LDA
amendment: ONEOK's 2025-04-28 filing amends its Q4 2024 report, Uber's
2025-10-23 amends Q1 and Q2. The LDA also accepts "No Activity" reports, which
say there was no lobbying at all. The filing type was in every API response and
was never stored.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analysis.tier2_detectors import detect_lobbying_overlaps
from src.db.models import LobbyingDisclosure, TransactionType
from src.ingestion.lda import ingest_lobbying_disclosures
from tests.test_lda import _client, northrop, resolver  # noqa: F401 - fixtures
from tests.test_tier2_detectors import _make_member, _make_trade

POSTED = datetime(2025, 4, 28)


def _filed(db, filing_type):
    member = _make_member(db)
    _make_trade(db, member, "OKE", TransactionType.SALE, POSTED + timedelta(days=5))
    db.add(
        LobbyingDisclosure(ticker="OKE", registrant="R", filed_date=POSTED, filing_type=filing_type)
    )
    db.commit()
    return detect_lobbying_overlaps(db)


@pytest.mark.parametrize("filing_type", ["4A", "1A", "MA", "RA", "YA", "4@", "Q1Y", "4AY"])
def test_an_amendment_or_a_no_activity_report_is_not_an_event(db_session, filing_type):
    assert _filed(db_session, filing_type) == []


@pytest.mark.parametrize("filing_type", ["Q1", "Q4", "RR", "MM", "YY", "2T"])
def test_a_report_of_lobbying_is(db_session, filing_type):
    assert len(_filed(db_session, filing_type)) == 1


def test_a_row_whose_type_was_never_stored_is_kept(db_session):
    assert len(_filed(db_session, None)) == 1


def test_the_ingester_stores_the_type(db_session, resolver, northrop):  # noqa: F811
    ingest_lobbying_disclosures(
        db_session, 2024, tickers=["NOC"], resolver=resolver, client=_client([northrop])
    )
    types = {row.filing_type for row in db_session.query(LobbyingDisclosure).all()}
    assert None not in types
    assert types <= {r["filing_type"] for r in northrop["results"]}


def test_a_rerun_fills_in_rows_stored_before_the_type_was(
    db_session,
    resolver,  # noqa: F811
    northrop,  # noqa: F811
):
    ingest_lobbying_disclosures(
        db_session, 2024, tickers=["NOC"], resolver=resolver, client=_client([northrop])
    )
    db_session.query(LobbyingDisclosure).update({"filing_type": None})
    db_session.commit()

    result = ingest_lobbying_disclosures(
        db_session, 2024, tickers=["NOC"], resolver=resolver, client=_client([northrop])
    )
    db_session.commit()

    assert result["imported"] == 0
    assert db_session.query(LobbyingDisclosure).filter_by(filing_type=None).count() == 0
