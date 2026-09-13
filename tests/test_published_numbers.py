"""What the site says about its own rigour has to be true.

Three separate claims were wrong, and all three erred in the direction of making
the analysis look stronger than it is -- which is the one direction this project
cannot afford.

1. The landing page counted `q_value IS NOT NULL` -- meaning WAS TESTED -- and
   published it under the heading "Findings that survive correction". The live
   site read 1 when exactly zero findings passed FDR and one had merely been
   testable. A q-value of 0.97 counted as surviving.

2. `multi_factor_risk` was assembled from the output of the three detectors
   disabled for producing indefensible arithmetic. The persist-time gate stops
   those rows being stored; it does not stop them being counted. So a member
   could be published as multi-factor risk on the strength of findings this
   project had already judged unfit to show -- and the disabled detectors were
   named in the description of a row that IS served.

3. It also counted findings rather than distinct types, so three findings of one
   type rendered as "3 different anomaly types" -- a single-signal member
   described as a multi-signal one, which is the whole content of the detector.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from src.analysis.catalog import DETECTORS
from src.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestTheCorrectionCardCountsWhatItSays:
    """It must report findings that PASSED, not findings that were tested."""

    def test_it_agrees_with_significance_summary(self, db_session):
        """Seeded rather than skipped: one finding of each kind, so the card and
        the summary are compared against a population that actually contains the
        distinction being tested."""
        from datetime import datetime

        from src.analysis.baselines import significance_summary
        from src.api.routes.dashboard_v2 import get_insights
        from src.db.models import Anomaly, Chamber, Member, Party

        member = Member(
            bioguide_id="PN00002",
            first_name="Mixed",
            last_name="Population",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="TX",
        )
        db_session.add(member)
        db_session.commit()

        for title, q in (("passed", 0.001), ("failed", 0.8), ("never tested", None)):
            db_session.add(
                Anomaly(
                    member_id=member.id,
                    anomaly_type="late_filing",
                    severity="high",
                    title=title,
                    description="x",
                    q_value=q,
                    detected_at=datetime(2024, 1, 1),
                )
            )
        db_session.commit()

        summary = significance_summary(db_session)
        assert summary["findings_passing_fdr"] == 1, summary
        assert summary["findings_with_a_null_model"] == 2, summary

        cards = asyncio.run(get_insights(db=db_session))
        card = next(c for c in cards if "correction" in c["title"].lower())

        assert card["value"] == f"{summary['findings_passing_fdr']:,}"
        assert card["value"] == "1", "the card counted tested findings, not passing ones"

    def test_a_tested_but_failing_finding_does_not_count_as_surviving(self, db_session):
        """The exact case that produced the wrong number in production."""
        from datetime import datetime

        from src.analysis.baselines import significance_summary
        from src.api.routes.dashboard_v2 import get_insights
        from src.db.models import Anomaly, Chamber, Member, Party

        member = Member(
            bioguide_id="PN00001",
            first_name="Pub",
            last_name="Numbers",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="OR",
        )
        db_session.add(member)
        db_session.commit()

        # q = 0.97: tested, and comprehensively failed.
        db_session.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="late_filing",
                severity="high",
                title="Tested and failed",
                description="x",
                q_value=0.97,
                p_value=0.9,
                detected_at=datetime(2024, 1, 1),
            )
        )
        db_session.commit()

        summary = significance_summary(db_session)
        assert summary["findings_with_a_null_model"] >= 1
        assert summary["findings_passing_fdr"] == 0

        cards = asyncio.run(get_insights(db=db_session))
        card = next(c for c in cards if "correction" in c["title"].lower())
        assert card["value"] == "0", "a finding with q=0.97 was reported as surviving correction"


class TestMultiFactorRiskExcludesDisabledDetectors:
    DISABLED = sorted(get_settings().disabled_anomaly_types_set)

    def _detector(self):
        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        return ExtendedAnomalyDetector()

    def _findings(self, types, member_id=1):
        return [
            {"member_id": member_id, "anomaly_type": t, "severity": "HIGH", "member_name": "X"}
            for t in types
        ]

    def test_three_disabled_findings_do_not_make_a_finding(self, db_session):
        assert len(self.DISABLED) >= 3, "this test needs three disabled types"

        results = self._detector().detect_red_flag_combinations(
            db_session, {"stock_anomalies": self._findings(self.DISABLED)}
        )

        assert results == [], (
            "a multi-factor finding was built entirely from detectors disabled for "
            f"producing indefensible output: {self.DISABLED}"
        )

    def test_a_disabled_type_is_never_named_in_a_published_finding(self, db_session):
        mixed = self._findings(
            [*self.DISABLED, "large_trade", "late_filing", "high_trading_frequency"]
        )

        results = self._detector().detect_red_flag_combinations(
            db_session, {"stock_anomalies": mixed}
        )

        for finding in results:
            named = set(finding.get("anomaly_types") or [])
            leaked = named & set(self.DISABLED)
            assert not leaked, f"disabled detectors named in a served finding: {leaked}"
            assert not (
                set(self.DISABLED) & set(re.findall(r"[a-z_]+", finding.get("description", "")))
            ), f"disabled detector named in the description: {finding.get('description')}"

    def test_genuine_multi_signal_members_still_fire(self, db_session):
        """The guard against fixing this by making the detector never fire."""
        results = self._detector().detect_red_flag_combinations(
            db_session,
            {"stock_anomalies": self._findings(["large_trade", "late_filing", "volume_spikes"])},
        )

        assert len(results) == 1, results
        assert results[0]["anomaly_type"] == "multi_factor_risk"


class TestMultiFactorRiskCountsDistinctTypes:
    def test_three_findings_of_one_type_are_not_three_types(self, db_session):
        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        same_type = [
            {
                "member_id": 1,
                "anomaly_type": "rapid_asset_appreciation",
                "severity": "HIGH",
                "member_name": "X",
            }
            for _ in range(3)
        ]

        results = ExtendedAnomalyDetector().detect_red_flag_combinations(
            db_session, {"asset_anomalies": same_type}
        )

        assert results == [], "three findings of a single type were published as multi-factor risk"

    def test_the_title_states_the_number_of_types(self, db_session):
        from src.analysis.extended_anomaly_detector import ExtendedAnomalyDetector

        findings = [
            {"member_id": 1, "anomaly_type": t, "severity": "HIGH", "member_name": "X"}
            for t in ("large_trade", "large_trade", "late_filing", "volume_spikes")
        ]

        results = ExtendedAnomalyDetector().detect_red_flag_combinations(
            db_session, {"stock_anomalies": findings}
        )

        assert len(results) == 1
        assert "3 different anomaly types" in results[0]["title"], results[0]["title"]


class TestTheReadmeMatchesTheCatalogue:
    """The table had drifted to 15 of 17 types, omitting `sponsorship_conflict`
    and `bill_jurisdiction_conflict` -- two of only six that carry a q-value, so
    the omission understated the suite precisely where it is strongest."""

    README = REPO_ROOT / "README.md"

    def _documented_types(self) -> set[str]:
        text = self.README.read_text()
        assert text.strip(), "README is empty -- this test would pass on nothing"
        return set(re.findall(r"^\| `([a-z_]+)` \|", text, flags=re.M))

    def test_every_detector_is_documented(self):
        documented = self._documented_types()
        catalogued = {d.anomaly_type for d in DETECTORS}

        assert not (catalogued - documented), (
            f"detectors missing from the README table: {sorted(catalogued - documented)}"
        )

    def test_the_readme_documents_no_detector_that_does_not_exist(self):
        documented = self._documented_types()
        catalogued = {d.anomaly_type for d in DETECTORS}

        assert not (documented - catalogued), (
            f"README documents types with no detector: {sorted(documented - catalogued)}"
        )

    def test_the_disabled_count_is_right(self):
        text = self.README.read_text()
        actual = len(get_settings().disabled_anomaly_types_set)
        words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}

        assert f"{words[actual]} detectors are disabled" in text, (
            f"README does not say {words[actual]} detectors are disabled, but {actual} are"
        )
