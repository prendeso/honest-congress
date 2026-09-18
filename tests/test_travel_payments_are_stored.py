"""The reader is half of it; something has to write the rows down.

This project has merged a correct fix connected to nothing before -- and an
unwired reader is the quietest possible version of that, because the parser
returns the trips, every unit test passes, and the table stays empty for ever.

So these drive `parse_disclosure` end to end and assert on rows in the database,
not on the parser's return value.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db.models import Chamber, Disclosure, Member, Party, TravelPayment

TRIPS = [
    {
        "source": "American Israel Education Foundation, Inc. (AIEF)",
        "start_date": datetime(2024, 3, 24),
        "end_date": datetime(2024, 3, 31),
        "itinerary": "Los Angeles, CA - Tel Aviv - Los Angeles, CA",
        "days_at_own_expense": 0,
    },
    {
        "source": "Center for Democracy in the Americas",
        "start_date": datetime(2024, 2, 19),
        "end_date": datetime(2024, 2, 22),
        "itinerary": "Washington, DC - Havana, Cuba - Seattle, WA",
        "days_at_own_expense": 1,
    },
]


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="TR00001",
        first_name="Trav",
        last_name="Eller",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="WA",
    )
    db_session.add(m)
    db_session.commit()
    return m


def annual(db, member, doc_id):
    filing = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="O",
        filing_date=datetime(2025, 5, 15),
        document_id=doc_id,
        document_url=f"https://disclosures-clerk.house.gov/{doc_id}.pdf",
        is_ptr=False,
    )
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return filing


def orchestrator(tmp_path, trips, *, assets=(), liabilities=()):
    from src.ingestion.orchestrator import IngestionOrchestrator

    orch = IngestionOrchestrator(data_dir=tmp_path)
    orch.download_disclosure_pdf = lambda d: tmp_path / "fd.pdf"  # type: ignore[method-assign]
    (tmp_path / "fd.pdf").write_bytes(b"%PDF-1.4")
    orch.disclosure_parser.parse_pdf = lambda path: {  # type: ignore[method-assign]
        "assets": list(assets),
        "liabilities": list(liabilities),
        "transactions": [],
        "travel_payments": [t.copy() for t in trips],
        "parse_errors": [],
        "raw_text": "S        A: A\nNone disclosed.\nS        B: T\nNone disclosed.\n"
        "S        D: L\nNone disclosed.\n",
    }
    return orch


def stored(db, filing):
    return (
        db.query(TravelPayment)
        .filter(TravelPayment.disclosure_id == filing.id)
        .order_by(TravelPayment.id)
        .all()
    )


class TestTheTripsReachTheDatabase:
    def test_every_trip_is_written(self, db_session, member, tmp_path):
        filing = annual(db_session, member, "TRAVEL-1")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)

        rows = stored(db_session, filing)
        assert [r.source for r in rows] == [t["source"] for t in TRIPS]

    def test_the_trip_keeps_its_dates_and_itinerary(self, db_session, member, tmp_path):
        filing = annual(db_session, member, "TRAVEL-2")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)

        havana = next(r for r in stored(db_session, filing) if "Havana" in (r.itinerary or ""))
        assert havana.source == "Center for Democracy in the Americas"
        assert (havana.start_date.month, havana.start_date.day) == (2, 19)
        assert (havana.end_date.month, havana.end_date.day) == (2, 22)
        assert havana.days_at_own_expense == 1

    def test_zero_days_at_own_expense_survives(self, db_session, member, tmp_path):
        # 0 means somebody else paid for all of it, which is the whole point of
        # the column. A falsy check anywhere turns it into "not stated".
        filing = annual(db_session, member, "TRAVEL-3")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)

        aief = next(r for r in stored(db_session, filing) if "AIEF" in r.source)
        assert aief.days_at_own_expense == 0

    def test_it_hangs_off_the_disclosure(self, db_session, member, tmp_path):
        filing = annual(db_session, member, "TRAVEL-4")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)
        db_session.refresh(filing)
        assert len(filing.travel_payments) == 2


class TestReReadingAFilingReplacesRatherThanAppends:
    """The defect `_clear_parsed_rows` exists for, one table further on.

    `_store_fd_data` only ever `db.add(...)` and `travel_payments` carries no
    uniqueness constraint, so a filing read twice would hold each trip twice --
    and a duplicate is indistinguishable from a member genuinely taking the same
    trip twice.
    """

    def test_two_parses_leave_two_rows_not_four(self, db_session, member, tmp_path):
        filing = annual(db_session, member, "TRAVEL-5")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)

        assert len(stored(db_session, filing)) == 2

    def test_a_filing_whose_only_rows_are_trips_still_clears(self, db_session, member, tmp_path):
        # `_clear_parsed_rows` returns early when `incoming` is empty, so travel
        # rows have to count as something to replace. A filing that discloses no
        # assets and no debts but four trips is exactly Valadao's shape.
        filing = annual(db_session, member, "TRAVEL-6")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)

        assert len(stored(db_session, filing)) == 2

    def test_a_parse_that_yields_nothing_does_not_delete_good_rows(
        self, db_session, member, tmp_path
    ):
        # The other half of the guard: a failed re-read must not empty a table
        # a previous read filled.
        filing = annual(db_session, member, "TRAVEL-7")
        orchestrator(tmp_path, TRIPS).parse_disclosure(db_session, filing)
        orchestrator(tmp_path, []).parse_disclosure(db_session, filing)

        assert len(stored(db_session, filing)) == 2


class TestItIsNotPublishedYet:
    """Deliberate, and asserted so that publishing it is a decision somebody
    makes rather than something that happens.

    The credentials this project leaked are still live, and a detector over this
    data would need its own evidence bar. Storing what the documents say is the
    part that is ready.
    """

    def test_no_api_route_serves_travel(self):
        from pathlib import Path

        served = Path("src/api/routes").rglob("*.py")
        offenders = [p.as_posix() for p in served if "travel_payment" in p.read_text()]
        assert offenders == [], (
            f"{offenders} serves travel data -- if that is intended, delete this test "
            "and say so in the commit, rather than letting it happen quietly"
        )

    def test_travel_is_not_a_detector_source(self):
        """The assertion that actually protects anybody.

        A finding about a named person derived from travel data would have to
        declare its source here, because `detectors_without_source_data` reports
        any detector whose input table is empty.
        """
        from src.analysis.baselines import DETECTOR_SOURCE_TABLES
        from src.db.models import TravelPayment

        sourced = [t for t, model in DETECTOR_SOURCE_TABLES.items() if model is TravelPayment]
        assert sourced == [], f"{sourced} produce findings from travel data"

    def test_only_the_corpus_quality_count_touches_travel_in_the_analysis_layer(self):
        """Counting rows is not publishing them, and the distinction is the point.

        `parse_quality_summary` counts travel rows so that a backfill storing
        zero of them says so out loud -- nothing else in the project reports on
        that table at all. It produces no `Anomaly` and names no member; it is
        read by `cli parse`'s operator summary.

        Every OTHER module under `src/analysis` is where a detector would live,
        so this is an exact allowlist rather than a substring search: adding
        travel to `tier2_detectors`, `legislation`, `clustering` or a new file
        fails here, which is the case the guard exists for.
        """
        from pathlib import Path

        allowed = {"src/analysis/baselines.py"}
        offenders = [
            p.as_posix()
            for p in Path("src/analysis").rglob("*.py")
            if "TravelPayment" in p.read_text() and p.as_posix() not in allowed
        ]
        assert offenders == [], (
            f"{offenders} read travel data. If that is a detector it needs the "
            "sample-reading bar D15 sets for any accuser, and a decision -- not "
            "an addition to this allowlist."
        )

    def test_no_template_shows_travel(self):
        from pathlib import Path

        offenders = [
            p.as_posix()
            for p in Path("src/templates").rglob("*.html")
            if "travel_payment" in p.read_text() or "travelPayment" in p.read_text()
        ]
        assert offenders == []
