"""Population baselines for detector output.

Every threshold in the suite is asserted rather than calibrated, so "exceeded
threshold 100" is hard to defend. Ranking a finding against others of its own
type says something that stands on its own. The suite also runs every detector
against every member, so a flag count needs its denominator reported alongside.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.analysis.baselines import (
    MIN_POPULATION_FOR_PERCENTILE,
    annotate_percentile_ranks,
    detection_summary,
    percentile_rank,
)
from src.db.models import Anomaly, Chamber, Member, Party


class TestPercentileRank:
    def test_largest_value_ranks_100(self):
        assert percentile_rank(10, [1, 2, 3, 10]) == 100.0

    def test_smallest_value_ranks_lowest(self):
        assert percentile_rank(1, [1, 2, 3, 10]) == 25.0

    def test_midpoint(self):
        assert percentile_rank(2, [1, 2, 3, 4]) == 50.0

    def test_ties_share_a_rank(self):
        assert percentile_rank(5, [5, 5, 5, 5]) == 100.0

    def test_empty_population_is_zero_not_an_error(self):
        assert percentile_rank(42, []) == 0.0

    def test_value_below_everything(self):
        assert percentile_rank(0, [1, 2, 3]) == 0.0


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="B000001",
        first_name="Base",
        last_name="Line",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="WA",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _seed(db, member, anomaly_type, values):
    for index, value in enumerate(values):
        db.add(
            Anomaly(
                member_id=member.id,
                anomaly_type=anomaly_type,
                severity="medium",
                title=f"{anomaly_type} #{index}",
                description="baseline fixture",
                computed_value=Decimal(str(value)),
            )
        )
    db.commit()


class TestAnnotatePercentileRanks:
    def test_ranks_within_a_populated_type(self, db_session, member):
        values = list(range(1, MIN_POPULATION_FOR_PERCENTILE + 1))
        _seed(db_session, member, "large_trade", values)

        annotate_percentile_ranks(db_session)

        rows = (
            db_session.query(Anomaly)
            .filter(Anomaly.anomaly_type == "large_trade")
            .order_by(Anomaly.computed_value)
            .all()
        )
        assert rows[-1].percentile_rank == 100.0
        assert rows[0].percentile_rank == pytest.approx(100 / len(values), abs=0.01)

    def test_small_populations_are_left_unranked(self, db_session, member):
        # Ranking 3 findings would make the largest "the 100th percentile",
        # which is noise dressed up as a statistic.
        _seed(db_session, member, "late_filing", [1, 2, 3])

        annotate_percentile_ranks(db_session)

        rows = db_session.query(Anomaly).filter(Anomaly.anomaly_type == "late_filing").all()
        assert all(r.percentile_rank is None for r in rows)

    def test_types_are_ranked_independently(self, db_session, member):
        # Dollars and days share no scale, so they must never be pooled.
        _seed(db_session, member, "large_trade", [10_000] * MIN_POPULATION_FOR_PERCENTILE)
        _seed(db_session, member, "high_trading_frequency", list(range(1, 21)))

        annotate_percentile_ranks(db_session)

        trades = db_session.query(Anomaly).filter(Anomaly.anomaly_type == "large_trade").all()
        assert all(t.percentile_rank == 100.0 for t in trades), "identical values all tie at 100"

        freq = (
            db_session.query(Anomaly)
            .filter(Anomaly.anomaly_type == "high_trading_frequency")
            .order_by(Anomaly.computed_value)
            .all()
        )
        assert freq[0].percentile_rank < freq[-1].percentile_rank

    def test_is_idempotent(self, db_session, member):
        _seed(db_session, member, "large_trade", list(range(1, 21)))

        first = annotate_percentile_ranks(db_session)
        second = annotate_percentile_ranks(db_session)

        assert first == second

    def test_rows_without_computed_value_are_skipped(self, db_session, member):
        db_session.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="multi_factor_risk",
                severity="high",
                title="no computed value",
                description="fixture",
            )
        )
        db_session.commit()

        annotate_percentile_ranks(db_session)

        row = db_session.query(Anomaly).filter(Anomaly.anomaly_type == "multi_factor_risk").one()
        assert row.percentile_rank is None


class TestDetectionSummary:
    def test_reports_the_denominator_not_just_the_findings(self, db_session, member):
        _seed(db_session, member, "large_trade", [1, 2, 3])

        summary = detection_summary(db_session)

        assert summary["total_findings"] == 3
        assert summary["members"] >= 1
        # The point of the summary: findings are meaningless without the number
        # of tests that produced them.
        assert summary["approximate_tests_run"] >= summary["detector_types_with_findings"]
        assert "not determinations of wrongdoing" in str(summary["caveat"])

    def test_per_type_breakdown_counts_distinct_members(self, db_session, member):
        _seed(db_session, member, "large_trade", [1, 2, 3, 4])

        summary = detection_summary(db_session)
        entry = next(e for e in summary["by_type"] if e["anomaly_type"] == "large_trade")

        assert entry["findings"] == 4
        assert entry["members_flagged"] == 1, "four findings from one member is one member"

    def test_empty_database_does_not_divide_by_zero(self, db_session):
        summary = detection_summary(db_session)

        assert summary["total_findings"] == 0
        assert summary["by_type"] == []


class TestDetectorsWithoutSourceData:
    """A detector with an empty input table cannot run.

    Reporting that separately matters more since QuiverQuant was dropped: the
    three Tier-2 tables have no ingester at all until FEC, Senate LDA and
    USASpending are built, so those detectors return nothing for want of data
    rather than because the data is clean. Without this distinction, an empty
    findings list reads as a clean bill of health.
    """

    def test_all_detectors_are_starved_on_an_empty_database(self, db_session):
        from src.analysis.baselines import DETECTOR_SOURCE_TABLES, detectors_without_source_data

        starved = detectors_without_source_data(db_session)

        assert len(starved) == len(DETECTOR_SOURCE_TABLES)
        assert {e["anomaly_type"] for e in starved} == set(DETECTOR_SOURCE_TABLES)

    def test_tier2_detectors_are_named_when_their_tables_are_empty(self, db_session):
        from src.analysis.baselines import detectors_without_source_data

        starved = {
            e["anomaly_type"]: e["empty_source_table"]
            for e in detectors_without_source_data(db_session)
        }

        assert starved["donor_conflict"] == "campaign_donations"
        assert starved["lobbying_overlap"] == "lobbying_disclosures"
        assert starved["contract_front_run"] == "government_contracts"

    def test_a_populated_table_clears_its_detectors(self, db_session, member):
        from datetime import datetime
        from decimal import Decimal

        from src.analysis.baselines import detectors_without_source_data
        from src.db.models import Disclosure, Transaction, TransactionType

        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=datetime(2024, 5, 1),
            document_id="SRC-1",
            is_ptr=True,
            parsed=True,
        )
        db_session.add(d)
        db_session.commit()
        db_session.refresh(d)
        db_session.add(
            Transaction(
                disclosure_id=d.id,
                transaction_date=datetime(2024, 3, 1),
                transaction_type=TransactionType.PURCHASE,
                description="AAPL",
                ticker="AAPL",
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
            )
        )
        db_session.commit()

        starved = {e["anomaly_type"] for e in detectors_without_source_data(db_session)}

        assert "large_trade" not in starved, "transactions exist, so this detector can run"
        assert "donor_conflict" in starved, "campaign_donations is still empty"

    def test_summary_surfaces_starved_detectors_and_says_why_it_matters(self, db_session):
        summary = detection_summary(db_session)

        assert summary["detectors_without_source_data"], "empty DB should report starved detectors"
        assert "detectors_without_source_data" in str(summary["caveat"])
