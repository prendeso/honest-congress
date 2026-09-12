"""Cross-member trade clustering.

Needs no price data, no position sizes and no assumption about intent -- only
dates, tickers and directions, all disclosed exactly. That makes it one of the
few timing-adjacent questions this data can actually support.

The hard part is the base rate: members trade popular large-caps constantly, so
a cluster has to be tighter than the ticker's general popularity explains.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.clustering import (
    CLUSTER_WINDOW_DAYS,
    MIN_MEMBERS_IN_CLUSTER,
    detect_cross_member_clusters,
)
from src.db.models import (
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

BASE = datetime(2024, 3, 1)

# Filler trades are deliberately spaced well beyond the clustering window.
# Several members sharing any ticker inside the window is a cluster, so filler
# bunched together would be found -- correctly -- as a second finding.


@pytest.fixture
def members(db_session):
    created = []
    for i in range(12):
        m = Member(
            bioguide_id=f"CL0000{i:02d}",
            first_name="Cluster",
            last_name=f"Member{i}",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
        )
        db_session.add(m)
        created.append(m)
    db_session.commit()
    for m in created:
        db_session.refresh(m)
    return created


def _trade(db, member, ticker, day_offset, direction=TransactionType.PURCHASE):
    d = (
        db.query(Disclosure)
        .filter(Disclosure.member_id == member.id, Disclosure.document_id == f"CL-{member.id}")
        .first()
    )
    if d is None:
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=BASE + timedelta(days=60),
            document_id=f"CL-{member.id}",
            is_ptr=True,
            parsed=True,
        )
        db.add(d)
        db.commit()
        db.refresh(d)

    db.add(
        Transaction(
            disclosure_id=d.id,
            transaction_date=BASE + timedelta(days=day_offset),
            transaction_type=direction,
            description=f"{ticker} stock",
            ticker=ticker,
            amount_min=Decimal("1001"),
            amount_max=Decimal("15000"),
        )
    )
    db.commit()


class TestClusterDetection:
    def test_tight_cluster_is_flagged(self, db_session, members):
        # 4 members buy the same niche ticker within a few days; the other 8
        # trade something else entirely, keeping ubiquity low.
        for i in range(MIN_MEMBERS_IN_CLUSTER):
            _trade(db_session, members[i], "NICHE", i)
        for i in range(MIN_MEMBERS_IN_CLUSTER, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        findings = detect_cross_member_clusters(db_session)

        assert len(findings) == 1
        assert findings[0]["ticker"] == "NICHE"
        assert findings[0]["member_count"] == MIN_MEMBERS_IN_CLUSTER

    def test_below_the_member_threshold_is_not_a_cluster(self, db_session, members):
        for i in range(MIN_MEMBERS_IN_CLUSTER - 1):
            _trade(db_session, members[i], "NICHE", i)

        assert detect_cross_member_clusters(db_session) == []

    def test_trades_spread_beyond_the_window_are_not_a_cluster(self, db_session, members):
        # Same members, same ticker, but spaced well outside the window.
        for i in range(MIN_MEMBERS_IN_CLUSTER):
            _trade(db_session, members[i], "NICHE", i * (CLUSTER_WINDOW_DAYS + 5))
        for i in range(MIN_MEMBERS_IN_CLUSTER, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        assert detect_cross_member_clusters(db_session) == []

    def test_a_small_slice_of_a_widely_held_ticker_is_suppressed(self, db_session, members):
        """Four of twelve MEGACAP holders overlapping is the base rate, not a signal.

        The filter is concentration, not popularity: what share of the ticker's
        traders fall inside the window. Here it is 4/12, so the overlap is
        better explained by the ticker being widely held.
        """
        for i in range(4):
            _trade(db_session, members[i], "MEGACAP", i)
        # The other eight hold it too, but spread far outside the window.
        for i in range(4, 12):
            _trade(db_session, members[i], "MEGACAP", 100 + i * (CLUSTER_WINDOW_DAYS + 5))

        assert detect_cross_member_clusters(db_session) == []

    def test_a_burst_covering_most_of_a_tickers_traders_is_kept(self, db_session, members):
        """Six of six is a burst, however many members that happens to be.

        Filtering on raw popularity would suppress exactly this -- in any
        population the largest clusters also involve the most members.
        """
        for i in range(6):
            _trade(db_session, members[i], "NICHE", i)
        for i in range(6, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        findings = detect_cross_member_clusters(db_session)

        assert len(findings) == 1
        assert findings[0]["member_count"] == 6
        assert findings[0]["cluster_concentration_percent"] == 100.0

    def test_opposite_directions_do_not_combine(self, db_session, members):
        # Two buyers and two sellers is not four members doing the same thing.
        for i in range(2):
            _trade(db_session, members[i], "NICHE", i, TransactionType.PURCHASE)
        for i in range(2, 4):
            _trade(db_session, members[i], "NICHE", i, TransactionType.SALE)
        for i in range(4, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        assert detect_cross_member_clusters(db_session) == []

    def test_one_member_trading_repeatedly_is_not_a_cluster(self, db_session, members):
        # Four trades, one member: a cluster counts distinct members.
        for offset in range(4):
            _trade(db_session, members[0], "NICHE", offset)
        for i in range(1, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        assert detect_cross_member_clusters(db_session) == []

    def test_description_reports_ubiquity_and_its_own_limits(self, db_session, members):
        for i in range(MIN_MEMBERS_IN_CLUSTER):
            _trade(db_session, members[i], "NICHE", i)
        for i in range(MIN_MEMBERS_IN_CLUSTER, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        description = detect_cross_member_clusters(db_session)[0]["description"]

        assert "everyone who has ever" in description
        assert "not coordination" in description
        assert "Filing dates are not trade dates" in description

    def test_larger_clusters_rank_first(self, db_session, members):
        for i in range(6):
            _trade(db_session, members[i], "BIGCLUSTER", i)
        for i in range(6, 10):
            _trade(db_session, members[i], "SMALLCLUSTER", i)
        for i in range(10, 12):
            _trade(db_session, members[i], "OTHER", 500 + i * (CLUSTER_WINDOW_DAYS + 5))

        findings = detect_cross_member_clusters(db_session)

        assert [f["ticker"] for f in findings] == ["BIGCLUSTER", "SMALLCLUSTER"]
        assert findings[0]["member_count"] > findings[1]["member_count"]

    def test_empty_database_is_not_an_error(self, db_session):
        assert detect_cross_member_clusters(db_session) == []

    def test_trades_without_tickers_are_ignored(self, db_session, members):
        for i in range(MIN_MEMBERS_IN_CLUSTER):
            _trade(db_session, members[i], None, i)

        assert detect_cross_member_clusters(db_session) == []
