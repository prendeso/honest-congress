"""Which filing becomes the baseline decides what this site accuses people of.

`wealth_analyzer.analyze_member` ordered a member's filings by `filing_year`
alone, and its growth loop skips any pair inside one year -- so the only
surviving comparison is last-filing-of-a-year against first-of-the-next, and
"last of the year" was left to whatever order the database returned.

Members hold up to six filings in one year, and among them can be a CANDIDATE
REPORT: filed by somebody running for the seat, before they hold it, describing
a private citizen's finances. Comparing that against their first member annual
measures a change of form, not a change of wealth.

Measured against the live corpus, two published findings were computed that way,
both naming sitting members:

    Craig Goldman   published $15,008,502.50 of growth in one year.
                    disc 664 "C"  2024 mid $4,675,002.00   <- the baseline used
                    disc 669 "H"  2024 mid $19,132,503.50  <- his own annual
                    disc 1455 "O" 2025 mid $19,683,504.50
                    19,683,504.50 - 4,675,002.00 = 15,008,502.50, to the cent.
                    Against his own annual the figure is $551,001.

    Laura Gillen    published $476,500.50; against her own annual, $75,499.50 --
                    below the $174,000 salary threshold, so the finding should
                    never have existed.

These tests pin both halves: the candidate report must never be a baseline, and
the choice among a member's own filings must be deterministic.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.wealth_analyzer import WealthAnalyzer, is_net_worth_snapshot
from src.db.models import Asset, AssetType, Chamber, Disclosure, Member, Party


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="G000001",
        first_name="Craig",
        last_name="Baseline",
        chamber=Chamber.HOUSE,
        party=Party.REPUBLICAN,
        state="TX",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _filing(db, member, *, year, filing_type, day, doc, worth):
    """One filing carrying a single asset worth exactly `worth`."""
    d = Disclosure(
        member_id=member.id,
        filing_year=year,
        filing_type=filing_type,
        filing_date=datetime(year, 1, day),
        document_id=doc,
        parsed=True,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    db.add(
        Asset(
            disclosure_id=d.id,
            asset_type=AssetType.STOCK,
            description="Holding",
            value_min=Decimal(worth),
            value_max=Decimal(worth),
        )
    )
    db.commit()
    return d


class TestACandidateReportIsNeverABaseline:
    def test_the_predicate_rejects_both_spellings(self, db_session, member):
        """The House stores a bare "C"; the Senate stores prose."""
        house = _filing(db_session, member, year=2024, filing_type="C", day=1, doc="H", worth=1)
        senate = _filing(
            db_session,
            member,
            year=2024,
            filing_type="Candidate Report  (Amendment 1)",
            day=2,
            doc="S",
            worth=1,
        )
        annual = _filing(db_session, member, year=2024, filing_type="O", day=3, doc="A", worth=1)

        assert is_net_worth_snapshot(house) is False
        assert is_net_worth_snapshot(senate) is False
        assert is_net_worth_snapshot(annual) is True

    def test_growth_is_measured_against_the_members_own_annual(self, db_session, member):
        """Goldman's shape, with his real figures rounded to whole dollars.

        The candidate report is the LAST 2024 filing by date, so before the fix
        it won the baseline outright. Measured against it the growth is
        $15,008,502; against his own annual it is $551,001.
        """
        _filing(db_session, member, year=2024, filing_type="H", day=1, doc="ANNUAL", worth=19132503)
        _filing(db_session, member, year=2024, filing_type="C", day=2, doc="CAND", worth=4675002)
        _filing(db_session, member, year=2025, filing_type="O", day=1, doc="NOW", worth=19683504)

        found = WealthAnalyzer().analyze_member(db_session, member.id)

        # $551,001 against a $174,000 salary is not 200% over, so nothing fires.
        assert found == [], f"expected no finding, got {[f['computed_value'] for f in found]}"

    def test_a_real_jump_is_still_reported(self, db_session, member):
        """The fix must not simply silence the detector."""
        _filing(db_session, member, year=2024, filing_type="H", day=1, doc="ANNUAL", worth=100_000)
        _filing(db_session, member, year=2025, filing_type="O", day=1, doc="NOW", worth=9_000_000)

        found = WealthAnalyzer().analyze_member(db_session, member.id)

        assert len(found) == 1
        assert found[0]["computed_value"] == Decimal("8900000.0")


class TestTheBaselineIsDeterministic:
    def test_the_latest_filing_of_a_year_is_the_baseline(self, db_session, member):
        """Within a year the later filing supersedes the earlier -- an amendment
        restates the original in full -- so the newest one is the year's figure.
        """
        _filing(db_session, member, year=2024, filing_type="H", day=1, doc="FIRST", worth=1_000_000)
        _filing(db_session, member, year=2024, filing_type="A", day=20, doc="AMEND", worth=200_000)
        _filing(db_session, member, year=2025, filing_type="O", day=1, doc="NOW", worth=5_000_000)

        found = WealthAnalyzer().analyze_member(db_session, member.id)

        # Baseline is the AMENDMENT (200,000), not the original (1,000,000).
        assert len(found) == 1
        assert found[0]["computed_value"] == Decimal("4800000.0")

    def test_the_order_does_not_depend_on_insertion_order(self, db_session, member):
        """The rows are created newest-first here. `filing_year` alone would let
        that decide the baseline; `filing_date` then `id` must not."""
        _filing(db_session, member, year=2024, filing_type="A", day=20, doc="AMEND", worth=200_000)
        _filing(db_session, member, year=2024, filing_type="H", day=1, doc="FIRST", worth=1_000_000)
        _filing(db_session, member, year=2025, filing_type="O", day=1, doc="NOW", worth=5_000_000)

        found = WealthAnalyzer().analyze_member(db_session, member.id)

        assert len(found) == 1
        assert found[0]["computed_value"] == Decimal("4800000.0")


class TestTheCorrectedFindingCanBePublished:
    def test_its_wording_is_not_one_the_old_detector_could_write(self, db_session, member):
        """`anomaly_key` identifies a member-level finding by its TITLE, so a
        corrected finding landing in the same growth bucket and year pair as a
        stale one is silently DISCARDED and the wrong dollar figure served for
        ever. The new title differs, and `_SUPERSEDED_WORDING` carries the entry
        that deletes the old rows -- this asserts the two agree."""
        from src.cli import _SUPERSEDED_WORDING

        _filing(db_session, member, year=2024, filing_type="H", day=1, doc="ANNUAL", worth=100_000)
        _filing(db_session, member, year=2025, filing_type="O", day=1, doc="NOW", worth=9_000_000)

        found = WealthAnalyzer().analyze_member(db_session, member.id)
        assert len(found) == 1

        purged = [
            needle
            for atype, field, needle in _SUPERSEDED_WORDING
            if atype == "excessive_wealth_growth" and field == "description"
        ]
        assert purged, "no purge entry guards this detector's old wording"

        for needle in purged:
            assert needle not in found[0]["description"], (
                f"the corrected detector still writes {needle!r}, so the purge "
                "would delete the findings it is meant to preserve"
            )

        assert "annual filings" in found[0]["title"]


class TestTheTwoSpellingsOfTheRuleAgree:
    """The same rule exists twice: a Python predicate for rows already loaded,
    and a SQL clause for counting without loading them.

    Two copies of one rule is how `filing_type == "FD"` survived -- a label that
    matched zero of 3,900 stored rows, gating BOTH advanced detectors, returning
    an empty roster in silence. `wealth_vs_salary` reading 0 findings looked like
    a fact about Congress; it was a fact about one line.

    So this asserts the two spellings classify every filing identically, over
    every `filing_type` the live corpus actually stores.
    """

    # The 33 distinct values on the live corpus, plus the shapes that matter.
    LIVE_TYPES = [
        "O",
        "A",
        "H",
        "T",
        "X",
        "C",
        "PTR",
        "G",
        "B",
        "W",
        "D",
        "E",
        "Annual Report",
        "Annual Report (Amendment)",
        "Annual Report for CY 2024",
        "Annual Report for CY 2025 (Amendment 2)",
        "Candidate Report",
        "Candidate Report  (Amendment 1)",
        "Candidate Report  (Amendment 3)",
        "New Filer Report for 01/21/2025",
        # Shapes the corpus does not currently hold but the parsers could write.
        "c",
        " C ",
        "candidate report",
        "",
        "  ",
    ]

    def test_every_stored_filing_type_is_classified_the_same_way(self, db_session, member):
        from src.analysis.wealth_analyzer import is_net_worth_snapshot, net_worth_snapshot_clause

        for index, filing_type in enumerate(self.LIVE_TYPES):
            db_session.add(
                Disclosure(
                    member_id=member.id,
                    filing_year=2024,
                    filing_type=filing_type,
                    filing_date=datetime(2024, 1, 1),
                    document_id=f"TYPE-{index}",
                    parsed=True,
                    is_ptr=filing_type == "PTR",
                )
            )
        db_session.commit()

        stored = db_session.query(Disclosure).filter(Disclosure.member_id == member.id).all()
        by_python = {d.id for d in stored if is_net_worth_snapshot(d)}
        by_sql = {
            d.id
            for d in db_session.query(Disclosure)
            .filter(Disclosure.member_id == member.id)
            .filter(net_worth_snapshot_clause())
            .all()
        }

        disagreed = by_python ^ by_sql
        assert not disagreed, "the Python predicate and the SQL clause disagree about " + str(
            sorted(d.filing_type for d in stored if d.id in disagreed)
        )

    def test_the_gate_no_longer_matches_a_label_nothing_writes(self, db_session, member):
        """`members_with_annual_filings` returned [] because it asked for a
        filing_type no ingester has written since the Senate stopped falling
        back to it."""
        from src.analysis import members_with_annual_filings

        for index, day in enumerate((1, 2)):
            db_session.add(
                Disclosure(
                    member_id=member.id,
                    filing_year=2023 + index,
                    filing_type="O",
                    filing_date=datetime(2023 + index, 1, day),
                    document_id=f"ANNUAL-{index}",
                    parsed=True,
                )
            )
        db_session.commit()

        found = members_with_annual_filings(db_session)

        assert [m.id for m in found] == [member.id], (
            "two annual filings should make a member comparable; the old gate "
            "asked for filing_type == 'FD' and found nobody at all"
        )

    def test_candidate_reports_do_not_make_a_member_comparable(self, db_session, member):
        """Two candidate reports are not two annual filings."""
        from src.analysis import members_with_annual_filings

        for index in (0, 1):
            db_session.add(
                Disclosure(
                    member_id=member.id,
                    filing_year=2023 + index,
                    filing_type="C",
                    filing_date=datetime(2023 + index, 1, 1),
                    document_id=f"CAND-{index}",
                    parsed=True,
                )
            )
        db_session.commit()

        assert members_with_annual_filings(db_session) == []
