"""The site published a $2,625,000 rise in net worth as "2625000.0%".

`formatValue` guessed the unit from the anomaly type's NAME:

    if (type.includes('growth') || type.includes('appreciation'))
        return value.toFixed(1) + '%';

`excessive_wealth_growth` contains "growth" and stores a DOLLAR amount --
`computed_value: Decimal(str(growth))`, the change in net worth, with
`threshold_value` the congressional salary over the same span. So every one of
that detector's findings rendered both figures as percentages, beside a named
member. Live, at the time of writing, all seven of them:

    Marlin Stutzman    2625000.0%      threshold 174000.0%
    Warren Davidson    3000000.5%      threshold 174000.0%
    Zoe Lofgren         962007.0%      threshold 174000.0%

Everything else fell through to a dollar sign applied indiscriminately, so a
45-day filing delay read "45.00" and 1,200 trades in a month would read "$1K".

The unit is now declared in `src/analysis/catalog.py`, beside the detector that
writes the number, and served through `/api/anomalies/types`. These tests run
the REAL function out of the REAL template in node -- not a copy of it here,
which would pass while the page stayed broken.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.analysis.catalog import DETECTORS, as_dicts

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "src" / "templates" / "anomalies.html"


class TestEveryDetectorDeclaresItsUnit:
    """The Python half, which needs no node."""

    def test_the_field_has_no_default(self):
        """A new detector must not be addable without saying what its numbers
        mean -- that omission is exactly how the percent sign got onto dollars."""
        import dataclasses

        field = {f.name: f for f in dataclasses.fields(DETECTORS[0])}["value_unit"]

        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_every_unit_is_one_the_page_can_render(self):
        known = {"dollars", "percent", "days", "count", "score"}

        for detector in DETECTORS:
            assert detector.value_unit in known, f"{detector.anomaly_type}: {detector.value_unit}"

    def test_the_api_serves_it(self):
        assert all("value_unit" in d for d in as_dicts())

    @pytest.mark.parametrize(
        "anomaly_type,unit",
        [
            # Read off each detector's own `computed_value` expression.
            ("excessive_wealth_growth", "dollars"),  # Decimal(str(growth))
            ("wealth_vs_salary", "dollars"),  # last_wealth - first_wealth
            ("large_trade", "dollars"),  # txn.amount_min
            ("volume_spikes", "dollars"),  # median + sigmas * MAD
            ("late_filing", "days"),  # days_to_file
            ("donor_conflict", "days"),  # days_apart
            ("lobbying_overlap", "days"),  # days_apart
            ("contract_front_run", "days"),  # days_before
            ("sponsorship_conflict", "days"),  # min(abs(...).days)
            ("bill_jurisdiction_conflict", "days"),  # min(abs(...).days)
            ("high_trading_frequency", "count"),  # count of trades
            ("trade_clustering", "count"),  # consecutive_same_direction
            ("cross_member_cluster", "count"),  # member_count
            ("sector_concentration", "percent"),  # concentration_percent
            ("committee_jurisdiction_conflict", "percent"),  # share * 100
            ("rapid_asset_appreciation", "percent"),  # guaranteed_growth_percent
            ("multi_factor_risk", "score"),  # total_score, no ceiling
        ],
    )
    def test_the_declared_unit_matches_what_the_detector_computes(self, anomaly_type, unit):
        declared = {d.anomaly_type: d.value_unit for d in DETECTORS}

        assert declared[anomaly_type] == unit

    def test_the_catalogue_covers_every_type_that_writes_a_value(self):
        """Anything emitting `computed_value` and absent here renders with no
        unit at all. The three that are absent must be exactly the three
        disabled detectors, whose findings are not served."""
        import ast

        emitting = set()
        for path in sorted((REPO / "src" / "analysis").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Dict):
                    continue
                pairs = {
                    k.value: v
                    for k, v in zip(node.keys, node.values, strict=True)
                    if isinstance(k, ast.Constant)
                }
                if "anomaly_type" not in pairs or "computed_value" not in pairs:
                    continue
                kind = pairs["anomaly_type"]
                if isinstance(kind, ast.Constant):
                    emitting.add(kind.value)

        catalogued = {d.anomaly_type for d in DETECTORS}

        assert emitting - catalogued == {
            "outperforming_trades",
            "perfect_timing",
            "loss_avoidance",
        }, "a live detector writes a number the catalogue declares no unit for"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
class TestThePageRendersTheDeclaredUnit:
    """Executes the template's own `formatValue`, in node.

    Copying the function into this file would have passed against the bug, so
    it is extracted from the shipped HTML instead.
    """

    @staticmethod
    def render(anomaly_type, value):
        html = TEMPLATE.read_text()

        body = re.search(r"\n(\s*)formatValue\(value, type\) \{.*?\n\1\},\n", html, re.S)
        assert body, "formatValue is no longer in the template under that signature"
        dollars = re.search(r"\n(\s*)formatDollars\(n\) \{.*?\n\1\}\n", html, re.S)
        assert dollars, "formatDollars is no longer in the template"

        types = json.dumps(as_dicts())
        script = f"""
        const detectorTypes = {types};
        const page = {{
            detectorFor(type) {{
                return detectorTypes.find(d => d.anomaly_type === type) || {{value_unit: null}};
            }},
            {body.group(0).strip().rstrip(",")},
            {dollars.group(0).strip()}
        }};
        process.stdout.write(page.formatValue({json.dumps(value)}, {json.dumps(anomaly_type)}));
        """

        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_a_net_worth_rise_is_not_a_percentage(self):
        """The live finding, verbatim: Marlin Stutzman, $2,625,000."""
        assert self.render("excessive_wealth_growth", 2625000.0) == "$2.6M"

    def test_the_salary_threshold_beside_it_is_dollars_too(self):
        assert self.render("excessive_wealth_growth", 174000.0) == "$174K"

    @pytest.mark.parametrize(
        "anomaly_type,value",
        [
            ("excessive_wealth_growth", 2625000.0),
            ("excessive_wealth_growth", 3000000.5),
            ("excessive_wealth_growth", 349003.0),
            ("wealth_vs_salary", 1200000.0),
            ("large_trade", 50000.0),
            ("volume_spikes", 87500.0),
        ],
    )
    def test_no_dollar_figure_carries_a_percent_sign(self, anomaly_type, value):
        assert "%" not in self.render(anomaly_type, value)

    @pytest.mark.parametrize(
        "anomaly_type,value,expected",
        [
            ("late_filing", 45.0, "45 days"),
            ("late_filing", 1.0, "1 day"),
            ("donor_conflict", 0.0, "0 days"),
            ("sector_concentration", 62.5, "62.5%"),
            ("committee_jurisdiction_conflict", 32.0, "32.0%"),
            ("high_trading_frequency", 701.0, "701"),
            ("high_trading_frequency", 1200.0, "1,200"),
            ("cross_member_cluster", 4.0, "4"),
            ("multi_factor_risk", 8.0, "8"),
            ("large_trade", 1001.0, "$1K"),
        ],
    )
    def test_each_unit_reads_as_itself(self, anomaly_type, value, expected):
        assert self.render(anomaly_type, value) == expected

    def test_a_count_of_trades_is_never_dollars(self):
        """1,200 trades in a month used to read "$1K", because anything over
        1000 got a dollar sign."""
        rendered = self.render("high_trading_frequency", 1200.0)

        assert "$" not in rendered and "K" not in rendered

    def test_zero_days_renders_rather_than_disappearing(self):
        """A donor conflict 0 days apart is a trade on the SAME DAY as the
        donation -- the strongest instance of that finding, and the one the
        old `if (!value) return '-'` blanked."""
        assert self.render("donor_conflict", 0.0) == "0 days"

    def test_a_retired_detectors_finding_gets_a_bare_number_not_a_guess(self):
        """`perfect_timing` is disabled, so it is not in the catalogue and no
        unit is known. Showing the number is honest; inventing "%" is not."""
        rendered = self.render("perfect_timing", 80.0)

        assert rendered == "80"
        assert "%" not in rendered and "$" not in rendered


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
class TestZeroIsNotHidden:
    def test_the_card_tests_for_null_rather_than_falsiness(self):
        """`x-if="anomaly.computed_value"` is false for 0, so the same-day
        donor conflict showed no Value row at all."""
        # Comments stripped first: the note explaining this quotes the old
        # binding verbatim, and matching that would be matching the fix.
        html = re.sub(r"<!--.*?-->", "", TEMPLATE.read_text(), flags=re.S)

        assert 'x-if="anomaly.computed_value"' not in html
        assert 'x-if="anomaly.threshold_value"' not in html
        assert "anomaly.computed_value !== null" in html
        assert "anomaly.threshold_value !== null" in html
