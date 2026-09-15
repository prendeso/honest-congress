"""The wealth detector tested a midpoint and published the result as a fact.

Disclosures report value BANDS. `_calculate_wealth_progression` builds three
numbers from them -- a midpoint estimate and the widest defensible interval --
and says in its own comment why:

    # Midpoint of each reported band. Disclosures report ranges, so this
    # is an estimate with real error bars -- the bounds are carried
    # alongside so callers can report them rather than imply precision.

`detect_wealth_vs_salary_anomalies` read `net_worth_estimate` and ignored both
bounds. It then published, under a member's name:

    "Net worth grew from $1,507,500 to $9,007,500 (7,500,000 total).
     Cumulative salary over 5 years: $870,000. Growth is 8.6x total possible
     salary accumulation."

Four point figures, none of which anybody disclosed, and a multiple computed
from two of them. That is D5 exactly: "Any point estimate derived from them is
fabricated precision, and every detector should publish the bounds it reasoned
over."

The deeper problem is not the wording. A member holding assets banded
$1,000,001-$5,000,000 has an interval wide enough that the midpoint clears any
threshold you choose while the filings remain entirely consistent with a salary
explaining everything. The title says growth "far exceeds salary". Held to the
midpoint that sentence can be false at every point the data actually permits.

So the bar now applies to the FLOOR of the interval: the smallest increase the
bands allow must itself be more than twice cumulative salary. Strictly
stronger, so it can only remove findings -- and what it removes are the ones
that rested on the width of a band rather than on any growth.

Nothing needed deleting: this detector had zero published findings when the
change was made, because the House annual filings it feeds on were the ones
lost to the filer-matching defect. It is fixed ahead of them arriving.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from src.analysis.advanced_anomaly_detector import AdvancedAnomalyDetector
from src.db.models import Asset, Chamber, Disclosure, Member, Party


def member(db, bioguide):
    row = Member(
        bioguide_id=bioguide,
        first_name="Test",
        last_name="Member",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def filing(db, member_row, year, doc_id, bands):
    disclosure = Disclosure(
        member_id=member_row.id,
        filing_year=year,
        filing_type="FD",
        filing_date=datetime(year, 6, 1),
        document_id=doc_id,
        is_ptr=False,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)

    for index, (low, high) in enumerate(bands):
        db.add(
            Asset(
                disclosure_id=disclosure.id,
                asset_type="stock",
                description=f"Holding {index}",
                value_min=low,
                value_max=high,
            )
        )
    db.commit()
    return disclosure


def findings(db, member_row):
    return [
        a
        for a in AdvancedAnomalyDetector().detect_wealth_vs_salary_anomalies(db)
        if a["member_id"] == member_row.id
    ]


# Read off the detector's own salary table rather than written down here: it
# is not a flat $174,000 across this span (2021 and 2022 each raised it), and
# a hardcoded figure would make these tests assert against a salary schedule
# that does not exist.
SALARY_2020_2024 = sum(
    AdvancedAnomalyDetector().get_salary_for_year(year) for year in range(2020, 2025)
)
BAR = 2 * SALARY_2020_2024


class TestTheBarAppliesToTheFloorNotTheMidpoint:
    def test_a_finding_the_bands_cannot_support_is_not_published(self, db_session):
        """One band, $1,000,001-$5,000,000, in both years -- the single most
        common band on the form. The midpoint says this member gained $4,000,000
        and the filings say the change could be anywhere from -$4,000,000 to
        +$4,000,000. The old condition fired; there is nothing here to report."""
        who = member(db_session, "W000100")
        filing(db_session, who, 2020, "FDa1", [(1000001, 5000000)])
        filing(db_session, who, 2024, "FDa2", [(1000001, 5000000)] * 2)

        # The midpoint growth the old code tested on, stated so this is a test
        # about the change and not about the fixture.
        midpoint_growth = (1000001 + 5000000) / 2
        assert midpoint_growth > BAR, "fixture no longer exercises the old condition"

        assert findings(db_session, who) == []

    def test_growth_the_narrowest_reading_still_shows_is_published(self, db_session):
        who = member(db_session, "W000101")
        filing(db_session, who, 2020, "FDb1", [(100000, 250000)])
        filing(db_session, who, 2024, "FDb2", [(5000001, 25000000)])

        result = findings(db_session, who)

        assert len(result) == 1
        # 5,000,001 - 250,000, which clears 2 x 870,000 with room to spare.
        assert result[0]["growth_low"] == pytest.approx(4750001)

    def test_the_floor_is_what_the_claim_rests_on(self, db_session):
        who = member(db_session, "W000102")
        filing(db_session, who, 2020, "FDc1", [(100000, 250000)])
        filing(db_session, who, 2024, "FDc2", [(5000001, 25000000)])

        finding = findings(db_session, who)[0]

        assert float(finding["computed_value"]) == pytest.approx(finding["growth_low"])
        assert float(finding["threshold_value"]) == SALARY_2020_2024
        assert float(finding["computed_value"]) > BAR

    def test_it_can_only_remove_findings_never_add_them(self, db_session):
        """The floor is never above the midpoint, so anything the new condition
        admits the old one admitted too."""
        who = member(db_session, "W000103")
        filing(db_session, who, 2020, "FDd1", [(100000, 250000)])
        filing(db_session, who, 2024, "FDd2", [(5000001, 25000000)])

        finding = findings(db_session, who)[0]
        midpoint_growth = (5000001 + 25000000) / 2 - (100000 + 250000) / 2

        assert finding["growth_low"] <= midpoint_growth
        assert finding["growth_low"] <= finding["growth_high"]


class TestThePublishedSentenceReportsBands:
    @pytest.fixture
    def finding(self, db_session):
        who = member(db_session, "W000104")
        filing(db_session, who, 2020, "FDe1", [(100000, 250000)])
        filing(db_session, who, 2024, "FDe2", [(5000001, 25000000)])
        return findings(db_session, who)[0]

    def test_both_ends_are_given_as_ranges(self, finding):
        description = finding["description"]

        assert "$100,000-$250,000" in description
        assert "$5,000,001-$25,000,000" in description

    def test_it_does_not_claim_a_net_worth_anybody_disclosed(self, finding):
        """The old sentence, verbatim: "Net worth grew from $1,507,500 to
        $9,007,500". Neither figure appears on any filing."""
        assert not re.search(r"Net worth grew from \$[\d,]+ to \$[\d,]+", finding["description"])

    def test_the_increase_is_stated_as_a_floor(self, finding):
        assert "at least $4,750,001" in finding["description"]

    def test_the_multiple_is_derived_from_that_floor(self, finding):
        """8.6x came from dividing one invented figure by another."""
        description = finding["description"]
        multiple = re.search(r"more than ([\d.]+)x", description)

        assert multiple, description
        assert float(multiple.group(1)) == pytest.approx(
            finding["growth_low"] / SALARY_2020_2024, abs=0.05
        )

    def test_the_reader_is_told_the_figures_are_bands(self, finding):
        assert "bands" in finding["description"]

    def test_the_sentence_has_no_stray_unsigned_dollar_amount(self, finding):
        """The old text rendered the total as "(7,500,000 total)" -- the dollar
        sign was simply missing from that one interpolation."""
        stripped = re.sub(r"\$[\d,]+", "", finding["description"])

        assert not re.search(r"\b\d{1,3}(,\d{3})+\b", stripped)


class TestTheDegenerateCaseIsUnchanged:
    def test_an_exact_value_behaves_as_before(self, db_session):
        """Where value_min == value_max there is no band, so floor, midpoint and
        ceiling coincide and the change is a no-op. This is the shape the
        existing suite's wealth tests use."""
        who = member(db_session, "W000105")
        filing(db_session, who, 2020, "FDf1", [(500000, 500000)])
        filing(db_session, who, 2024, "FDf2", [(10000000, 10000000)])

        finding = findings(db_session, who)[0]

        assert finding["growth_low"] == finding["growth_high"] == pytest.approx(9500000)
