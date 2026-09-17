"""One asset, one year, one entry — and an order the database does not choose.

`rapid_asset_appreciation` compares CONSECUTIVE entries for the same normalized
asset name. It built those entries one per stored ROW, so a member who holds the
same thing in two accounts — and therefore lists it twice in a single filing —
had those two rows compared against each other, as though one had grown into the
other over no time at all.

"Fidelity Inv. - IRA Cash [IH]" appears twice in Warren Davidson's 2024 annual.
Both at $1 - $1,000 there, so it happens to be harmless; the same shape at
$1 - $1,000 and $500,001 - $1,000,000 yields "49,900% appreciation (2024-2024)"
at CRITICAL severity, under the member's name.

`years_diff` is 0 for such a pair. That is guarded against dividing by zero, but
the guard makes `annual_growth` fall back to the raw growth figure, and
`annual_growth > 500` fires on it anyway.

Which of the two rows counted as "before" was decided by the query planner:
`_assets_by_disclosure` had no ORDER BY. The same defect class as the pagination
and wealth-baseline bugs — an ordering that is not total is a result that is not
reproducible.

The detector is disabled today, which is the only reason none of this is
published. It is fixed before it is re-enabled, not after.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.analysis.advanced_anomaly_detector import AdvancedAnomalyDetector, _assets_by_disclosure
from src.db.models import Asset, AssetType, Chamber, Disclosure, Member, Party


@pytest.fixture
def member(db_session):
    member = Member(
        bioguide_id="AP00001",
        first_name="App",
        last_name="Reciation",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="OH",
    )
    db_session.add(member)
    db_session.commit()
    return member


def filing(db_session, member, year: int, document_id: str) -> Disclosure:
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=year,
        filing_type="O",
        filing_date=datetime(year + 1, 5, 1),
        document_id=document_id,
        document_url=f"https://disclosures-clerk.house.gov/{document_id}.pdf",
        is_ptr=False,
        parsed=True,
        parse_confidence=1.0,
    )
    db_session.add(disclosure)
    db_session.commit()
    return disclosure


def holding(db_session, disclosure, description, low, high) -> Asset:
    asset = Asset(
        disclosure_id=disclosure.id,
        asset_type=AssetType.OTHER,
        description=description,
        value_min=low,
        value_max=high,
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def appreciation(db_session):
    found = AdvancedAnomalyDetector().detect_asset_appreciation_anomalies(db_session)
    return [a for a in found if a["anomaly_type"] == "rapid_asset_appreciation"]


class TestTwoRowsInOneFilingAreOneHolding:
    def test_the_same_asset_twice_in_one_year_is_not_appreciation(self, db_session, member):
        # The shape that produces "49,900% appreciation (2024-2024)".
        first = filing(db_session, member, 2023, "AP-2023")
        holding(db_session, first, "Fidelity Inv. - IRA Cash", 1, 1000)

        second = filing(db_session, member, 2024, "AP-2024")
        holding(db_session, second, "Fidelity Inv. - IRA Cash", 1, 1000)
        holding(db_session, second, "Fidelity Inv. - IRA Cash", 500001, 1000000)

        for finding in appreciation(db_session):
            assert finding["start_year"] != finding["end_year"], (
                f"compared two rows of one filing against each other: {finding['title']}"
            )

    def test_the_two_rows_are_summed_for_the_year(self, db_session, member):
        # What the member holds of that asset that year is the total across the
        # accounts they hold it in, so the comparison should see that total
        # rather than either row alone.
        first = filing(db_session, member, 2023, "SUM-2023")
        holding(db_session, first, "Municipal bond ladder", 1000, 1000)

        second = filing(db_session, member, 2024, "SUM-2024")
        holding(db_session, second, "Municipal bond ladder", 1000, 1000)
        holding(db_session, second, "Municipal bond ladder", 2000, 2000)

        # Neither row on its own clears the bar -- $1,000 -> $1,000 is no growth
        # and $1,000 -> $2,000 is exactly 100%, which the threshold excludes. The
        # $3,000 the member actually holds does.
        (finding,) = appreciation(db_session)
        assert (finding["start_year"], finding["end_year"]) == (2023, 2024)
        assert finding["end_value"] == 3000

    def test_a_genuine_year_over_year_rise_still_fires(self, db_session, member):
        # The guard must not silence the thing the detector is for.
        first = filing(db_session, member, 2023, "REAL-2023")
        holding(db_session, first, "winery business", 15000, 15000)

        second = filing(db_session, member, 2024, "REAL-2024")
        holding(db_session, second, "winery business", 1000000, 1000000)

        (finding,) = appreciation(db_session)
        assert (finding["start_year"], finding["end_year"]) == (2023, 2024)
        assert finding["growth_percent"] > 1000


class TestTheRowsComeBackInAStatedOrder:
    """Asserted on the SQL, not on the rows that come back.

    SQLite hands back rows in insertion order whether or not the query asks for
    one, so a test that compares the returned ids passes identically with and
    without the ORDER BY -- verified by removing it and watching the test still
    pass. Production is Postgres, where the order is the planner's to choose.

    A test that cannot fail on the database it runs against is not a test of
    this, so this asks what SQL was actually emitted.
    """

    @staticmethod
    def sql_emitted_by(db_session, call) -> list[str]:
        from sqlalchemy import event

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", record)
        try:
            call()
        finally:
            event.remove(bind, "before_cursor_execute", record)
        return statements

    def test_the_asset_query_states_a_total_order(self, db_session, member):
        disclosure = filing(db_session, member, 2024, "ORD-2024")
        for n in range(3):
            holding(db_session, disclosure, f"Holding {n}", 1000, 2000)

        statements = self.sql_emitted_by(
            db_session, lambda: _assets_by_disclosure(db_session, [disclosure.id])
        )

        (select,) = [s for s in statements if "FROM assets" in s]
        assert "ORDER BY" in select, (
            "no ORDER BY, so which row counts as 'before' is the query planner's "
            "choice rather than the data's"
        )
        assert "assets.id" in select.split("ORDER BY", 1)[1], (
            "ordered, but not by anything unique -- ties are still arbitrary"
        )

    def test_the_liability_query_states_one_too(self, db_session, member):
        from src.analysis.advanced_anomaly_detector import _liabilities_by_disclosure

        disclosure = filing(db_session, member, 2024, "ORDL-2024")

        statements = self.sql_emitted_by(
            db_session, lambda: _liabilities_by_disclosure(db_session, [disclosure.id])
        )

        (select,) = [s for s in statements if "FROM liabilities" in s]
        # `split("ORDER BY")[-1]` alone returns the WHOLE statement when there is
        # no ORDER BY, and `liabilities.id` is in the SELECT list, so the check
        # passed with the clause removed. Assert the clause exists first.
        assert "ORDER BY" in select
        assert "liabilities.id" in select.split("ORDER BY", 1)[1]
