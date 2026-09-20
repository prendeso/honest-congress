"""Three detectors published sentences that were wrong about their own numbers.

All three name real members of Congress on a public page.

    "Between more than 100 stock trades were made in March 2026, which exceeds
     the threshold of 10 trades per month."          (24 findings)

    title       "Multi-factor risk: 3 different anomaly types"
    description "Member matched 4 different detectors (combined score: 8/10)."
    ...followed by a list of the three type names.                (27 findings)

    "4 members saled NVDA within 1 days"

The first is two templates collided: a ladder of closed bands ("25-50") plus one
open phrase ("more than 100"), interpolated into `f"Between {trade_range}"`.
The second counted findings where its own title counted distinct types, and
divided by a maximum that does not exist — `total_score` adds 0-3 per finding
with no ceiling, so six HIGH findings publish "12/10". The third coined a verb
from `f"{direction}d"` where direction is "purchase" or "sale".

These assert against what the detectors actually emit, not against strings
chosen here, so they fail if the wording regresses in any direction.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analysis.trade_analyzer import TradeAnalyzer


class _Txn:
    """The attributes `_check_trading_frequency` reads. It touches no database."""

    def __init__(self, when, ticker="AAPL", description="Apple Inc. (AAPL) [ST]"):
        # Grouped by disclosure first, then by month.
        self.disclosure_id = 99
        self.transaction_date = when
        self.ticker = ticker
        self.transaction_type = None
        self.amount_min = Decimal("1001")
        self.amount_max = Decimal("15000")
        # The asset class the form printed. `asset_class` reads this to decide
        # what noun the sentence may use.
        self.description = description


def frequency_findings(count, month=datetime(2026, 3, 2)):
    txns = [_Txn(month + timedelta(days=i % 27)) for i in range(count)]
    return TradeAnalyzer()._check_trading_frequency(txns, member_id=1, member=None)


class TestTheTradeCountIsPublishedAsItWasDisclosed:
    @pytest.mark.parametrize("count", [11, 12, 26, 40, 90, 264, 701])
    def test_the_exact_count_appears_in_both_title_and_description(self, count):
        finding = frequency_findings(count)[0]

        assert f"{count} trades" in finding["title"]
        # "stock" used to sit here and was never checked against anything: a
        # PTR row carries no asset type, so Sen. Rick Scott's eighteen
        # municipal bonds were published as eighteen stock trades. What this
        # test is for is the exact COUNT surviving into both strings rather
        # than a band, and that is unchanged.
        assert f"{count} transactions" in finding["description"]
        assert "stock" not in finding["description"].lower()
        assert int(finding["computed_value"]) == count

    @pytest.mark.parametrize("count", [11, 40, 701])
    def test_no_band_survives_anywhere_in_the_text(self, count):
        finding = frequency_findings(count)[0]
        text = f"{finding['title']} {finding['description']}"

        for band in ("10-15", "15-25", "25-50", "50-100", "more than 100"):
            assert band not in text

    def test_the_sentence_does_not_open_with_a_stranded_preposition(self):
        """The reported defect, exactly: "Between more than 100 stock trades"."""
        description = frequency_findings(701)[0]["description"]

        assert not re.search(r"\bBetween (more|less|over|under|at least)\b", description)

    @pytest.mark.parametrize("count", [11, 40, 701])
    def test_the_description_reads_as_a_sentence(self, count):
        description = frequency_findings(count)[0]["description"]

        assert description[0].isupper() or description[0].isdigit()
        assert description.rstrip().endswith(".")
        assert "  " not in description

    def test_the_lowest_count_it_can_produce_is_described_correctly(self):
        """It fires on `count > threshold`, so 11 is the floor. The old band
        claimed 10 in the same sentence that named 10 as the threshold."""
        assert frequency_findings(10) == []

        finding = frequency_findings(11)[0]
        assert "11 trades" in finding["title"]
        assert "threshold of 10" in finding["description"]

    def test_it_no_longer_asserts_what_the_page_beneath_it_denies(self):
        """The catalogue's `limits` renders directly below this description and
        says a managed account can produce this without the member choosing it.
        The detector used to answer that it "may indicate active trading based
        on non-public information"."""
        description = frequency_findings(701)[0]["description"]

        assert "non-public information" not in description


class TestMultiFactorRiskAgreesWithItself:
    def _findings(self, types_and_severities):
        return [
            {
                "member_id": 7,
                "member_name": "A Member",
                "anomaly_type": t,
                "severity": s,
            }
            for t, s in types_and_severities
        ]

    def _detect(self, findings, db_session):
        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        return ExtendedAnomalyDetector().detect_red_flag_combinations(
            db_session, {"stock_anomalies": findings}
        )

    def test_the_description_counts_what_the_title_counts(self, db_session):
        """Four findings, three types. The title said 3 and the description 4."""
        result = self._detect(
            self._findings(
                [
                    ("large_trade", "HIGH"),
                    ("large_trade", "HIGH"),
                    ("late_filing", "HIGH"),
                    ("volume_spikes", "HIGH"),
                ]
            ),
            db_session,
        )
        assert result, "the fixture no longer trips the detector"
        finding = result[0]

        assert "3 different anomaly types" in finding["title"]
        assert "3 different detectors" in finding["description"]
        assert "4 different detectors" not in finding["description"]

    def test_the_finding_count_is_still_reported_just_not_as_the_type_count(self, db_session):
        result = self._detect(
            self._findings(
                [
                    ("large_trade", "HIGH"),
                    ("large_trade", "HIGH"),
                    ("late_filing", "HIGH"),
                    ("volume_spikes", "HIGH"),
                ]
            ),
            db_session,
        )

        assert "4 findings" in result[0]["description"]

    def test_the_score_is_not_published_over_an_invented_maximum(self, db_session):
        """`total_score` adds 0-3 per finding and has no ceiling: these four
        HIGH findings score 8, and six would score 12 — published as "12/10"."""
        result = self._detect(
            self._findings(
                [
                    ("large_trade", "HIGH"),
                    ("large_trade", "HIGH"),
                    ("late_filing", "HIGH"),
                    ("volume_spikes", "HIGH"),
                ]
            ),
            db_session,
        )
        description = result[0]["description"]

        assert "/10" not in description
        assert "8" in description


class TestAClusterTitleIsGrammatical:
    def _cluster(self, direction):
        from src.db.models import TransactionType

        return TransactionType.SALE if direction == "sale" else TransactionType.PURCHASE

    @pytest.mark.parametrize(
        "direction,verb,wrong", [("sale", "sold", "saled"), ("purchase", "bought", "purchased")]
    )
    def test_the_verb_is_a_real_one(self, direction, verb, wrong):
        from src.analysis.clustering import _PAST_TENSE

        assert _PAST_TENSE[direction] == verb
        assert _PAST_TENSE[direction] != wrong

    @pytest.mark.parametrize("days,expected", [(1, "1 day"), (2, "2 days"), (30, "30 days")])
    def test_a_single_day_is_not_plural(self, days, expected):
        from src.analysis.clustering import _days

        assert _days(days) == expected

    def test_every_stored_direction_has_a_verb(self):
        """`_cluster_key` can only ever produce these two; a third would render
        as a KeyError rather than as silent nonsense, which is the point."""
        from src.analysis.clustering import _PAST_TENSE

        assert set(_PAST_TENSE) == {"purchase", "sale"}


class TestTheAlreadyPublishedFindingsAreRemoved:
    """Correcting the code does not correct the site.

    `persist_anomalies` only inserts, and `anomaly_key` identifies a member-level
    finding by its title. A corrected title is therefore a NEW finding published
    beside the broken one; a corrected description keeps its identity and is
    never rewritten at all.
    """

    def _anomaly(self, db, **kw):
        from src.db.models import Anomaly, Chamber, Member, Party

        member = db.query(Member).first()
        if member is None:
            member = Member(
                bioguide_id="W00001",
                first_name="A",
                last_name="Member",
                chamber=Chamber.HOUSE,
                party=Party.DEMOCRAT,
                state="CA",
            )
            db.add(member)
            db.commit()

        row = Anomaly(member_id=member.id, severity="medium", **kw)
        db.add(row)
        db.commit()
        return row

    def _purge(self, db_session, dry_run=False):
        # The command opens its own session; point it at the test's, the way
        # tests/test_purge_non_awards.py already does.
        from argparse import Namespace
        from contextlib import contextmanager
        from unittest.mock import patch

        from src.cli import cmd_purge_stale_wording

        @contextmanager
        def _fake_get_db():
            yield db_session

        with patch("src.cli.get_db", _fake_get_db):
            cmd_purge_stale_wording(Namespace(dry_run=dry_run))

    def test_a_band_titled_finding_is_deleted(self, db_session):
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="high_trading_frequency",
            title="High trading activity: more than 100 trades in March 2026",
            description="Between more than 100 stock trades were made in March 2026.",
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 0

    def test_a_stale_multi_factor_description_is_deleted(self, db_session):
        """Its title was already correct, so nothing else would ever rewrite it."""
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="multi_factor_risk",
            title="Multi-factor risk: 3 different anomaly types",
            description="Member matched 4 different detectors (combined score: 8/10).",
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 0

    def test_a_midpoint_net_worth_sentence_is_deleted(self, db_session):
        """`wealth_vs_salary` keeps its title when corrected, so nothing else
        would ever rewrite the description -- the same trap as multi_factor_risk.
        The figures below are band midpoints and appear on no filing."""
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="wealth_vs_salary",
            title="Wealth growth far exceeds salary (2020-2024)",
            description=(
                "Net worth grew from $1,507,500 to $9,007,500 (7,500,000 total). "
                "Cumulative salary over 5 years: $934,500. Growth is 8.0x total "
                "possible salary accumulation."
            ),
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 0

    def test_a_clustering_finding_claiming_a_short_period_is_deleted(self, db_session):
        """The rule read no dates, so "within a short period" described runs
        spanning years -- Casten's 11 trades covered 1,131 days. All 169 live
        findings carry the phrase, and the corrected detector cannot write it."""
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="trade_clustering",
            title="Consecutive same-direction trades (7 in a row)",
            description=(
                "Member made 7 consecutive trades in the same direction (all buys "
                "or all sells) within a short period. This describes the sequence "
                "only; it does not measure timing, profitability, or intent."
            ),
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 0

    def test_the_previous_correction_is_superseded_in_its_turn(self, db_session):
        """This finding was itself the fix for the one above, and it is now
        wrong for a different reason.

        Stating the real span ("over 12 days") corrected the missing time
        window. It left "consecutive ... in a row" in place, which asserts an
        ORDER -- and a PTR records a date, not a time, so trades sharing a date
        have none. Rep. Blake Moore's "30 in a row over 10 days" was 37 trades
        on ONE date, ordered by the Clerk's alphabetical asset listing.

        So the title goes the way the description went, and this test asserts
        the opposite of what it asserted before. That is the point of keeping
        it: a correction is not a resting place, and the row that carried the
        last one has to come down too.
        """
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="trade_clustering",
            title="Consecutive same-direction trades (7 in a row)",
            description=(
                "Member made 7 consecutive trades in the same direction (all buys "
                "or all sells) over 12 days. This describes the sequence only; it "
                "does not measure timing, profitability, or intent."
            ),
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 0

    def test_the_current_clustering_wording_survives(self, db_session):
        """The other half: the purge must not eat what the detector writes now.

        Without this the rule above degenerates into "delete every
        trade_clustering finding", which passes every deletion test and breaks
        the site.
        """
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="trade_clustering",
            title="Same-direction trades on one day (8)",
            description=(
                "Member made 8 sales on 19 January 2024. The filing records a date "
                "but no time of day, so this is a batch rather than a sequence: it "
                "says what was traded that day, not in what order. This describes "
                "what was traded only; it does not measure timing, profitability, "
                "or intent."
            ),
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 1

    def test_todays_six_needles_spare_what_the_detectors_now_write(self, db_session):
        """The other half of six rules added in one day.

        Two of them are short and generic -- `("late_filing", "description",
        "Trade: ")` and `("high_trading_frequency", "description", "which
        exceeds the threshold of")`. A rule that also matched the CORRECTED
        sentence would delete every finding of its type every night and let the
        next `analyze` re-derive it, for ever. So each corrected sentence is
        seeded here verbatim, as the detectors emit it today, and must survive.

        Checked once against a real corpus as well: 460 of 1,617 stored
        findings carry text the current detectors cannot write, and none of the
        460 findings they DO produce matches any needle.
        """
        from src.db.models import Anomaly

        corrected = [
            (
                "late_filing",
                "Late PTR filing: significantly late (1-3 months)",
                "Transaction on 2024-04-08 was filed significantly late (1-3 months) "
                "on 2024-09-01. The STOCK Act requires filing within 45 days. Trade "
                "reported: purchase of Cleveland-Cliffs Inc. Common Stock, which the "
                "filing reports for their spouse.",
            ),
            (
                "high_trading_frequency",
                "High trading activity: 15 trades in September 2024",
                "15 transactions were attributed to this member in September 2024, "
                "above the threshold of 10 per month. All of them are dated 11 "
                "September 2024; a PTR records a date but no time of day.",
            ),
            (
                "sector_concentration",
                "High concentration in finance sector (3 of 5)",
                "3 of the 5 transactions attributed to this member in this filing "
                "are in the finance sector (60%), above the 50% threshold.",
            ),
            (
                "trade_clustering",
                "Exchanges on one day (12)",
                "The filing reports 12 exchanges on 30 September 2024. The form "
                "marks these `E`: holdings converted in kind rather than bought or "
                "sold.",
            ),
            (
                "committee_jurisdiction_conflict",
                "Traded finance while serving on overseeing committee",
                "3 of 13 trades attributed to this member (23%) are in the finance "
                "sector, while the member serves on House Committee on Financial "
                "Services.",
            ),
        ]
        for anomaly_type, title, description in corrected:
            self._anomaly(
                db_session, anomaly_type=anomaly_type, title=title, description=description
            )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == len(corrected), (
            "a purge needle added today also matches the corrected sentence, so "
            "the rule deletes and re-derives that finding every night"
        )

    def test_a_corrected_finding_is_left_alone(self, db_session):
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="high_trading_frequency",
            title="High trading activity: 701 trades in March 2026",
            description="701 stock trades were disclosed in March 2026.",
        )
        self._anomaly(
            db_session,
            anomaly_type="cross_member_cluster",
            title="4 members sold NVDA within 1 day",
            description="4 members disclosed a sale of NVDA.",
        )
        self._anomaly(
            db_session,
            anomaly_type="wealth_vs_salary",
            title="Wealth growth far exceeds salary (2020-2024)",
            description=(
                "Reported net worth went from a range of $100,000-$250,000 in 2020 "
                "to $5,000,001-$25,000,000 in 2024. On the least favourable reading "
                "of those bands the increase is still at least $4,750,001."
            ),
        )

        self._purge(db_session)

        assert db_session.query(Anomaly).count() == 3

    def test_dry_run_deletes_nothing(self, db_session):
        from src.db.models import Anomaly

        self._anomaly(
            db_session,
            anomaly_type="cross_member_cluster",
            title="4 members saled NVDA within 1 days",
            description="4 members disclosed a sale of NVDA.",
        )

        self._purge(db_session, dry_run=True)

        assert db_session.query(Anomaly).count() == 1
