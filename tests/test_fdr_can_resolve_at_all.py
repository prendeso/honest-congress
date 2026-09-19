"""Nothing in the suite asserted that a finding can EVER pass FDR.

Every existing significance test asserts a q-value *exists*
(`test_a_coincidence_detector_gets_a_q_value`), or asserts the pass count is
zero (`test_baselines.py`, `test_significance.py:409`). None asks whether a pass
is reachable at all. So this went unnoticed:

Production run 228 reported `2539 tests, 0 passing FDR at alpha=0.050`. That was
not a fact about Congress. A permutation p-value cannot fall below the
Phipson-Smyth floor of 1/(permutations+1) -- 1/1001 = 0.000999 at the configured
1000 -- and Benjamini-Hochberg gives the most extreme test `q = p * n / 1`. With
n = 2539:

    q = 0.000999 * 2539 = 2.54   ->   clamped to 1.0

**The single most extreme timing alignment in the corpus would have been
reported at q = 1.0 and hidden.** For a lone standout to pass, the run would
need ~50,779 permutations rather than 1,000, or at least 51 tests
simultaneously tied at the floor.

Every one of the 4,744 annotated findings therefore carried q > alpha and was
dropped from the default response, leaving only the 1,549 untested ones visible.
The page discloses the withholding honestly; what nothing disclosed was that the
withholding was forced by arithmetic rather than by evidence.
"""

from __future__ import annotations

import pytest

from src.analysis.significance import benjamini_hochberg


def floor_for(permutations: int) -> float:
    """The Phipson-Smyth floor a permutation p-value cannot go below."""
    return 1.0 / (permutations + 1)


class TestTheFloorMeetsTheFamilySize:
    def test_the_production_configuration_could_not_resolve(self):
        # The exact numbers from run 228, kept as a regression anchor.
        q = benjamini_hochberg([floor_for(1000)] + [0.5] * 2538)
        assert min(q) > 0.05, (
            "a lone test at the permutation floor should be unresolvable at "
            "n=2539 with 1000 permutations -- if this now passes, the floor or "
            "the family size changed and the guard needs revisiting"
        )

    def test_enough_permutations_make_the_same_test_pass(self):
        # Same data, same family size, only the resolution changed. This is the
        # half that proves the failure is configuration and not evidence.
        q = benjamini_hochberg([floor_for(60_000)] + [0.5] * 2538)
        assert min(q) <= 0.05

    def test_a_smaller_family_makes_the_same_test_pass(self):
        # The other lever: correct within a detector family rather than pooling
        # every test in the run into one.
        q = benjamini_hochberg([floor_for(1000)] + [0.5] * 39)
        assert min(q) <= 0.05


class TestTheRunSaysWhenItCannotResolve:
    """The guard. It changes no published number -- it names the condition."""

    def _summary(self, db, **kwargs):
        from src.analysis.significance import annotate_significance

        return annotate_significance(db, **kwargs)

    def test_an_empty_run_is_not_reported_as_unresolvable(self, db_session):
        # No tests means nothing to resolve; flagging it would cry wolf on every
        # empty database.
        assert self._summary(db_session, permutations=50)["fdr_is_resolvable"] is True

    def test_the_summary_carries_the_floor_it_used(self, db_session):
        summary = self._summary(db_session, permutations=999)
        assert summary["p_value_floor"] == pytest.approx(1 / 1000, abs=1e-9)

    def test_the_production_family_size_is_reported_unresolvable(self):
        # The case the guard exists for, asserted directly on the condition
        # rather than through a fixture that would need 2,539 real tests.
        from src.analysis.significance import fdr_is_resolvable

        assert fdr_is_resolvable(2539, 1000, 0.05) is False

    def test_a_family_the_floor_can_clear_is_resolvable(self):
        from src.analysis.significance import fdr_is_resolvable

        assert fdr_is_resolvable(40, 1000, 0.05) is True

    def test_more_permutations_rescue_the_production_family(self):
        from src.analysis.significance import fdr_is_resolvable

        assert fdr_is_resolvable(2539, 1000, 0.05) is False
        assert fdr_is_resolvable(2539, 60_000, 0.05) is True

    def test_an_empty_family_is_resolvable_not_broken(self):
        from src.analysis.significance import fdr_is_resolvable

        assert fdr_is_resolvable(0, 1000, 0.05) is True


class TestClusterTestsDoNotPadTheDenominator:
    """`_cluster_p_values` claimed to return a p-value per flagged cluster.

    It tested every ticker instead. A ticker one member traded alone has
    `observed == 1`, every shuffle trivially matches it, and the p-value is
    exactly 1.0 however many permutations run. Those rows can never pass and
    can never help another test's rank -- they only raise `n`, and BH multiplies
    every other q-value by n/rank.
    """

    def test_a_lone_members_ticker_is_deterministically_one(self):
        import numpy as np

        from src.analysis.clustering import CLUSTER_WINDOW_DAYS
        from src.analysis.significance import _max_members_in_window, cluster_p_value

        dates = {1: [float(d) for d in range(0, 360, 30)]}
        marks = [(w, m) for m, ds in dates.items() for w in ds]
        observed = _max_members_in_window(marks, CLUSTER_WINDOW_DAYS)
        assert observed == 1

        p = cluster_p_value(
            dates,
            CLUSTER_WINDOW_DAYS,
            observed,
            permutations=200,
            rng=np.random.default_rng(3),
        )
        assert p == 1.0, "a test that cannot do anything but return 1.0 is denominator padding"

    def test_the_significance_bar_matches_the_detectors_bar(self):
        # The detector requires MIN_MEMBERS_IN_CLUSTER before it will flag
        # anything; the significance side must not test what the detector would
        # never publish.
        from pathlib import Path

        from src.analysis.clustering import MIN_MEMBERS_IN_CLUSTER

        source = Path("src/analysis/significance.py").read_text()
        assert "if observed < MIN_MEMBERS_IN_CLUSTER:" in source, (
            "the cluster null tests every ticker again; its docstring says it "
            "tests what the detector flagged"
        )
        assert MIN_MEMBERS_IN_CLUSTER == 4
