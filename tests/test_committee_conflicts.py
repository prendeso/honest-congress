"""Committee jurisdiction conflicts, rebuilt on real assignment data.

The detector this replaces had no committee data at all -- its assignment table
was an empty dict -- so it substring-matched tickers against sector keyword
lists. "ba" matched "Alibaba", making Alibaba a defense holding. It also emitted
its findings as `sector_concentration`, colliding with an unrelated
TradeAnalyzer detector of the same name.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.committee_conflicts import (
    ANOMALY_TYPE,
    MIN_TRADES_IN_SECTOR,
    detect_committee_jurisdiction_conflicts,
)
from src.analysis.sectors import classify, committee_sectors
from src.db.models import (
    Chamber,
    CommitteeAssignment,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


class TestSectorClassification:
    def test_exact_ticker_beats_substring(self):
        # The whole point: "BA" is Boeing, and must not make "Alibaba" defense.
        assert classify("BA") == {"defense"}
        assert "defense" not in classify(None, "Alibaba Group Holding")
        assert classify("BABA") == set()

    def test_short_tickers_do_not_match_inside_words(self):
        # "T" (AT&T) and "GS" (Goldman) were catastrophic as substrings.
        assert classify("T") == {"telecom"}
        assert classify(None, "Target Corporation") == set()
        assert classify(None, "Kellogg's") == set()

    def test_keywords_match_on_word_boundaries(self):
        assert classify(None, "First National Bank") == {"finance"}
        assert classify(None, "Eurobankers Holding") == set()

    def test_ticker_is_authoritative_over_description(self):
        # A recognised ticker should not also pick up unrelated keyword hits.
        assert classify("LMT", "Lockheed Martin pharmaceutical division") == {"defense"}

    def test_unknown_input_returns_nothing(self):
        assert classify(None, None) == set()
        assert classify("ZZZZ", "some obscure holding") == set()


class TestCommitteeSectors:
    def test_known_committee(self):
        assert committee_sectors("SSAS") == frozenset({"defense"})

    def test_subcommittee_inherits_the_parent(self):
        assert committee_sectors("SSAF13") == committee_sectors("SSAF")

    def test_unknown_committee_is_empty_not_an_error(self):
        assert committee_sectors("ZZZZ") == frozenset()
        assert committee_sectors(None) == frozenset()


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="CC00001",
        first_name="Comm",
        last_name="Conflict",
        chamber=Chamber.SENATE,
        party=Party.REPUBLICAN,
        state="VA",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


@pytest.fixture
def disclosure(db_session, member):
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 4, 1),
        document_id="CC-1",
        is_ptr=True,
        parsed=True,
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _assign(db, member, committee_id, name="Senate Committee on Armed Services"):
    db.add(
        CommitteeAssignment(
            member_id=member.id,
            committee_id=committee_id,
            committee_name=name,
            chamber=Chamber.SENATE,
        )
    )
    db.commit()


def _trade(db, disclosure, ticker, description="holding", owner="Self"):
    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 3, 1),
            transaction_type=TransactionType.PURCHASE,
            description=description,
            ticker=ticker,
            amount_min=Decimal("1001"),
            amount_max=Decimal("15000"),
            owner=owner,
        )
    )
    db.commit()


class TestDetector:
    def test_flags_trading_in_an_overseen_sector(self, db_session, member, disclosure):
        _assign(db_session, member, "SSAS")
        for ticker in ["LMT", "RTX", "NOC", "GD"]:
            _trade(db_session, disclosure, ticker)

        findings = detect_committee_jurisdiction_conflicts(db_session)

        assert len(findings) == 1
        assert findings[0]["anomaly_type"] == ANOMALY_TYPE
        assert findings[0]["sector"] == "defense"
        assert findings[0]["member_id"] == member.id

    def test_does_not_flag_unrelated_sectors(self, db_session, member, disclosure):
        _assign(db_session, member, "SSAS")  # defense
        for ticker in ["AAPL", "MSFT", "NVDA", "AMD"]:
            _trade(db_session, disclosure, ticker)

        assert detect_committee_jurisdiction_conflicts(db_session) == []

    def test_member_with_no_committee_is_skipped(self, db_session, member, disclosure):
        for ticker in ["LMT", "RTX", "NOC", "GD"]:
            _trade(db_session, disclosure, ticker)

        assert detect_committee_jurisdiction_conflicts(db_session) == []

    def test_below_the_minimum_trade_count_is_not_flagged(self, db_session, member, disclosure):
        _assign(db_session, member, "SSAS")
        for ticker in ["LMT", "RTX"][: MIN_TRADES_IN_SECTOR - 1]:
            _trade(db_session, disclosure, ticker)

        assert detect_committee_jurisdiction_conflicts(db_session) == []

    def test_incidental_holding_in_a_large_portfolio_is_not_flagged(
        self, db_session, member, disclosure
    ):
        _assign(db_session, member, "SSAS")
        for ticker in ["LMT", "RTX", "NOC"]:
            _trade(db_session, disclosure, ticker)
        # Swamp them: 3 of 40 trades is not a pattern.
        for index in range(37):
            _trade(db_session, disclosure, None, f"unclassified holding {index}")

        assert detect_committee_jurisdiction_conflicts(db_session) == []

    def test_subcommittee_seat_counts(self, db_session, member, disclosure):
        _assign(db_session, member, "SSAF13", name="Agriculture - a subcommittee")
        for ticker in ["ADM", "BG", "CTVA", "DE"]:
            _trade(db_session, disclosure, ticker)

        findings = detect_committee_jurisdiction_conflicts(db_session)

        assert len(findings) == 1
        assert findings[0]["sector"] == "agriculture"

    def test_description_states_what_it_does_not_establish(self, db_session, member, disclosure):
        _assign(db_session, member, "SSAS")
        for ticker in ["LMT", "RTX", "NOC", "GD"]:
            _trade(db_session, disclosure, ticker)

        description = detect_committee_jurisdiction_conflicts(db_session)[0]["description"]

        assert "does not establish" in description
        assert "no timing or price analysis" in description

    def test_alibaba_is_not_a_defense_contractor(self, db_session, member, disclosure):
        """The regression this detector exists to not repeat."""
        _assign(db_session, member, "SSAS")
        for index in range(6):
            _trade(db_session, disclosure, "BABA", f"Alibaba Group Holding {index}")

        assert detect_committee_jurisdiction_conflicts(db_session) == []
