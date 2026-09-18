"""A held detector should cost nothing, not cost the same and be thrown away.

Three of the six disabled types read `Asset` and `Liability` rows:
`wealth_vs_salary`, `rapid_asset_appreciation` and `excessive_wealth_growth`.
All three ran on every `analyze`; only their output was discarded, by
`persist_anomalies` and by the type filter inside `analyze_all_members`.

That was affordable while a House annual yielded half its holdings and every
Senate annual yielded none. The re-parse campaign ended that: 2,174 filings
re-read, roughly twice the holdings per House annual, and 343 Senate annuals
that now return assets where they returned nothing. The work these three do is
proportional to those rows.

`daily-update.yml` allows the nightly 350 minutes, and the file's own comment
records run 225 spending 234 of them on the contracts feed alone. This is the
margin, spent on rows the project has already judged unfit to publish.

The claim under test is not "the method was not called" -- that is the
mechanism. It is that **no asset row is read** while the type is held. So these
assert on the SQL the session actually emitted, which is what costs the time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import event

from src.analysis.advanced_anomaly_detector import run_advanced_anomaly_detection
from src.analysis.wealth_analyzer import WealthAnalyzer, analyze_wealth
from src.db.models import Asset, AssetType, Chamber, Disclosure, Liability, Member, Party
from tests.conftest import enabling_anomaly_type as enabling

HELD = ("wealth_vs_salary", "rapid_asset_appreciation", "excessive_wealth_growth")


@pytest.fixture
def roster(db_session):
    """Two members, three annual filings each, assets and a debt on every one.

    Enough that a detector which runs at all has to read asset rows to do it.
    """
    for n in range(2):
        member = Member(
            bioguide_id=f"HD{n:05d}",
            first_name="Held",
            last_name=f"Member{n}",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="TX",
        )
        db_session.add(member)
        db_session.commit()

        for year, value in ((2020, 500_000), (2022, 900_000), (2024, 20_000_000)):
            disclosure = Disclosure(
                member_id=member.id,
                filing_year=year,
                filing_type="O",
                filing_date=datetime(year + 1, 5, 15),
                document_id=f"HD{n}-{year}",
                document_url=f"https://disclosures-clerk.house.gov/HD{n}-{year}.pdf",
                is_ptr=False,
                parsed=True,
                parse_confidence=1.0,
            )
            db_session.add(disclosure)
            db_session.commit()

            db_session.add(
                Asset(
                    disclosure_id=disclosure.id,
                    asset_type=AssetType.STOCK,
                    description="Portfolio",
                    value_min=Decimal(value),
                    value_max=Decimal(value),
                )
            )
            db_session.add(
                Liability(
                    disclosure_id=disclosure.id,
                    creditor="Bank",
                    liability_type="Mortgage",
                    amount_min=Decimal(10_000),
                    amount_max=Decimal(15_000),
                )
            )
        db_session.commit()
    return db_session


def sql_from(engine, call) -> list[str]:
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return statements


def reads_holdings(statements: list[str]) -> list[str]:
    """Statements that touch the tables the re-parse just doubled."""
    return [s for s in statements if "FROM assets" in s or "FROM liabilities" in s]


class TestTheAdvancedPairIsNotRunWhileHeld:
    def test_no_asset_row_is_read_while_both_are_held(self, roster, engine):
        statements = sql_from(engine, lambda: run_advanced_anomaly_detection(roster, persist=False))
        assert reads_holdings(statements) == [], (
            f"a held detector read the holdings tables anyway: {reads_holdings(statements)[:2]}"
        )

    @pytest.mark.parametrize("anomaly_type", ("wealth_vs_salary", "rapid_asset_appreciation"))
    def test_it_reads_them_again_once_enabled(self, roster, engine, monkeypatch, anomaly_type):
        # The other half of the mutation check: without this, deleting the
        # detector's body entirely would pass the test above.
        with enabling(anomaly_type, monkeypatch):
            statements = sql_from(
                engine, lambda: run_advanced_anomaly_detection(roster, persist=False)
            )
        assert reads_holdings(statements), f"{anomaly_type} was enabled and still read nothing"

    def test_the_result_still_carries_the_keys_the_cli_prints(self, roster):
        result = run_advanced_anomaly_detection(roster, persist=False)
        for key in ("wealth_anomalies", "asset_anomalies", "stock_anomalies", "total"):
            assert key in result
        assert result["wealth_anomalies"] == []
        assert result["asset_anomalies"] == []


class TestTheWealthAnalyzerIsNotRunWhileHeld:
    """`excessive_wealth_growth` is the only type `WealthAnalyzer` emits.

    So while it is held, every query this analyzer makes is waste -- and the
    expensive ones are the two SUMs over a member's assets and liabilities.
    """

    def test_the_roster_pass_issues_no_query_at_all(self, roster, engine):
        # Not merely "reads no assets": the per-member gate alone would give
        # that, because `analyze_member` returns before its first query -- I
        # checked, by removing the roster gate and watching this pass. The
        # roster pass costs two queries of its own (a GROUP BY ... HAVING over
        # disclosures and a Member IN) plus a walk of ~450 members and a commit,
        # all to produce nothing. Asserting on the whole statement list is what
        # makes removing THIS gate fail.
        statements = sql_from(engine, lambda: WealthAnalyzer().analyze_all_members(roster))
        assert statements == [], f"a held analyzer queried anyway: {statements[:2]}"

    def test_the_roster_pass_reads_them_once_enabled(self, roster, engine, monkeypatch):
        with enabling("excessive_wealth_growth", monkeypatch):
            statements = sql_from(engine, lambda: WealthAnalyzer().analyze_all_members(roster))
        assert reads_holdings(statements)

    def test_the_summary_shape_is_unchanged(self, roster):
        # `cli analyze` reads every one of these to print its results block, and
        # the admin dashboard reads the same dict.
        result = WealthAnalyzer().analyze_all_members(roster)
        for key in ("members_analyzed", "members_with_anomalies", "total_anomalies", "anomalies"):
            assert key in result
        assert result["total_anomalies"] == 0
        assert result["anomalies"] == []

    def test_one_member_asked_for_directly_is_held_too(self, roster, engine):
        # `analyze_wealth(db, member_id)` and the /detect route reach
        # `analyze_member` without passing through `analyze_all_members`, so the
        # gate on the roster pass alone would leave them running.
        member = roster.query(Member).first()
        statements = sql_from(engine, lambda: analyze_wealth(roster, member_id=member.id))
        assert reads_holdings(statements) == []

    def test_one_member_asked_for_directly_runs_when_enabled(self, roster, engine, monkeypatch):
        member = roster.query(Member).first()
        with enabling("excessive_wealth_growth", monkeypatch):
            statements = sql_from(engine, lambda: analyze_wealth(roster, member_id=member.id))
        assert reads_holdings(statements)


class TestTheseThreeAreStillHeld:
    @pytest.mark.parametrize("anomaly_type", HELD)
    def test_it_is_disabled_by_default(self, anomaly_type):
        # Every assertion above reads "held" from the settings. If one of these
        # is re-enabled, those tests silently stop testing anything, so say so
        # here instead.
        from src.config import Settings

        assert anomaly_type in Settings().disabled_anomaly_types_set


class TestEveryHeldTypeIsActuallySkipped:
    """`src/config.py` claims each disabled type is skipped, not discarded.

    Adding a seventh entry to `DISABLED_ANOMALY_TYPES` gets you the persist-time
    gate for free -- `persist_anomalies` refuses any type in the set -- and that
    is the gate whose absence nobody notices, because the findings do disappear.
    What you do not get for free is the detector not RUNNING, which is the half
    that costs the nightly its wall clock. This fails when the two drift apart.
    """

    def test_each_one_is_named_in_a_gate(self):
        from pathlib import Path

        from src.config import get_settings

        gated = set()
        for path in Path("src/analysis").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for anomaly_type in get_settings().disabled_anomaly_types_set:
                if f'detector_is_disabled("{anomaly_type}")' in text:
                    gated.add(anomaly_type)

        missing = sorted(get_settings().disabled_anomaly_types_set - gated)
        assert not missing, (
            f"disabled but still computed and discarded: {missing}. Gate each one "
            "at the site that runs it, the way the other detectors are."
        )
