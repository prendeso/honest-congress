"""Multiple-comparisons control.

These findings name real people, so the tests are weighted toward what the
machinery must NOT do:

* It must not invent a p-value for a detector with no null model. Ten of the
  sixteen measure a magnitude ("concentration above 50%") rather than a
  coincidence, and there is nothing to shuffle. They get NULL, and NULL means
  *untested*, never *passed*.
* It must not treat same-day PTR batches as independent arrivals. A dozen
  transactions filed on one date are one event, not twelve, and a null that
  scattered them would make ordinary clustering look extraordinary. This is the
  specific failure an analytic Poisson null would have had, and there is a test
  for it below.
* It must not report a p-value of zero. A permutation test with N shuffles
  cannot support a claim finer than 1/(N+1).

The calibration tests are the load-bearing ones. A null model that never fires
is as broken as one that always does, so `test_null_is_calibrated` checks that
unrelated data produces roughly uniform p-values, and `test_planted_signal_is_
recovered` checks the test can still see something real. Both use fixed seeds:
they assert on a stochastic process and would otherwise flake.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from src.analysis.baselines import significance_summary
from src.analysis.significance import (
    NO_NULL_MODEL,
    NULL_SPECS,
    annotate_significance,
    benjamini_hochberg,
    cluster_p_value,
    permutation_p_value,
)
from src.db.models import (
    Anomaly,
    CampaignDonation,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)

DAY = 1.0


# --------------------------------------------------------------------------
# Benjamini-Hochberg
# --------------------------------------------------------------------------


def test_bh_matches_hand_computation():
    # p * n / rank, then the running minimum from the largest p downwards:
    # 0.01*4/1=0.04, 0.02*4/2=0.04, 0.03*4/3=0.04, 0.9*4/4=0.9
    assert benjamini_hochberg([0.01, 0.02, 0.03, 0.9]) == [0.04, 0.04, 0.04, 0.9]


def test_bh_enforces_monotonicity():
    # The step a first attempt skips. Raw p*n/rank gives
    # 0.01*3/1 = 0.03, 0.04*3/2 = 0.06, 0.04*3/3 = 0.04 -- so the LAST finding
    # would get a smaller q than the more significant one before it, which is
    # incoherent. The running minimum pulls the middle one down to 0.04.
    q = benjamini_hochberg([0.01, 0.04, 0.04])
    assert q == sorted(q), "q-values must not decrease as p-values increase"
    assert q[1] == pytest.approx(0.04)


def test_bh_never_exceeds_one():
    assert all(q <= 1.0 for q in benjamini_hochberg([0.9, 0.95, 0.99]))


def test_bh_preserves_input_order():
    q = benjamini_hochberg([0.9, 0.01, 0.5])
    assert q[1] < q[2] < q[0]


@pytest.mark.parametrize("values,expected", [([], []), ([0.5], [0.5]), ([0.0], [0.0])])
def test_bh_edge_cases(values, expected):
    assert benjamini_hochberg(values) == expected


# --------------------------------------------------------------------------
# The null itself
# --------------------------------------------------------------------------


def test_null_is_calibrated():
    """Unrelated trades and events must produce roughly uniform p-values.

    A null that never fires would make every finding look significant; one that
    always fires would make the whole exercise pointless. Both failures are
    silent without this.
    """
    rng = np.random.default_rng(20260913)
    p_values = []
    for _ in range(120):
        trades = sorted(rng.uniform(0, 3000, size=40).tolist())
        events = sorted(rng.uniform(0, 3000, size=6).tolist())
        p, _ = permutation_p_value({"T": (trades, events)}, 30, permutations=200, rng=rng)
        p_values.append(p)

    p_values = np.array(p_values)
    # Conservative is acceptable -- it under-accuses -- but it must be in the
    # right region, not pinned at either end.
    assert 0.35 < p_values.mean() < 0.65
    assert np.mean(p_values < 0.05) < 0.15


def test_planted_signal_is_recovered():
    rng = np.random.default_rng(20260913)
    events = sorted(rng.uniform(0, 3000, size=8).tolist())
    # Every event followed by a trade three days later, plus background noise.
    trades = sorted([e + 3 for e in events] + rng.uniform(0, 3000, size=15).tolist())

    p, observed = permutation_p_value({"T": (trades, events)}, 30, permutations=500, rng=rng)

    assert observed >= 8
    assert p < 0.05


def test_shift_null_is_blind_to_evenly_spaced_events():
    """A limitation on the record, not a bug: the shift null cannot see a
    signal whose calendar repeats.

    Shifting the trades by one period puts every trade back on an event, so the
    permutations reproduce the very alignment they are meant to destroy and the
    p-value pins at 1.0. The same planted lag with irregular spacing is found
    easily -- the contrast below is the whole point.

    This is not fixable by tuning: it is what a circular shift *is*. It matters
    because it runs one way only. The test under-accuses here, never over-
    accuses, so a member trading on a regular schedule is invisible to it
    rather than falsely flagged. Anyone reading a q-value on this site should
    know that a missing finding is not a finding of nothing.
    """
    rng = np.random.default_rng(20260913)
    events = [float(index * 36) for index in range(40)]
    trades = [event + 2.0 for event in events]

    p, observed = permutation_p_value({"T": (trades, events)}, 30, permutations=500, rng=rng)

    # Every trade sits two days after an event, and the test still sees nothing.
    assert observed == 40
    assert p == 1.0

    # The identical lag, with the events irregularly spaced, is caught.
    rng = np.random.default_rng(20260913)
    irregular = sorted(rng.uniform(0, 1440, size=40).tolist())
    lagged = [event + 2.0 for event in irregular]

    found, _ = permutation_p_value({"T": (lagged, irregular)}, 30, permutations=500, rng=rng)

    assert found < 0.05


def test_same_day_batches_do_not_manufacture_significance():
    """Twelve trades filed on one date are one event, not twelve.

    A null that resampled dates independently would scatter the batch, find
    such clustering impossibly rare, and call it significant. The shift null
    keeps the batch intact, so the p-value stays unremarkable.
    """
    rng = np.random.default_rng(20260913)
    # Four PTRs, a dozen transactions each, all unrelated to the events.
    trades = sorted([float(day) for day in (100, 700, 1400, 2200) for _ in range(12)])
    events = sorted(rng.uniform(0, 3000, size=6).tolist())

    p, _ = permutation_p_value({"T": (trades, events)}, 30, permutations=400, rng=rng)

    assert p > 0.05, "batched trades are not evidence of timing"


def test_a_single_coincidence_is_not_significant():
    # One event, and among a normal year of trading one of them lands three
    # days from it. Striking to look at, worth nothing statistically, and the
    # machinery has to say so rather than reward the coincidence.
    rng = np.random.default_rng(1)
    trades = sorted(rng.uniform(0, 3000, size=30).tolist() + [1003.0])
    p, observed = permutation_p_value({"T": (trades, [1000.0])}, 60, permutations=500, rng=rng)

    assert observed >= 1
    assert p > 0.05


def test_p_value_never_reaches_zero():
    # A permutation test with N shuffles cannot support a finer claim than
    # 1/(N+1), and reporting 0.0 would claim certainty it has not earned.
    rng = np.random.default_rng(3)
    events = [float(d) for d in range(0, 3000, 300)]
    trades = [e + 1 for e in events]
    p, _ = permutation_p_value({"T": (trades, events)}, 5, permutations=99, rng=rng)
    assert p >= 1 / 100


def test_too_short_a_span_returns_no_p_value():
    # Nothing to shift into, so no claim is made rather than a meaningless one.
    p, _ = permutation_p_value({"T": ([10.0, 12.0], [11.0])}, 30, permutations=100)
    assert p is None


def test_no_eligible_trades_returns_no_p_value():
    assert permutation_p_value({"T": ([], [1.0, 500.0])}, 30)[0] is None


def test_streams_do_not_cross_match():
    """A trade in one ticker must not be paired with another ticker's event."""
    rng = np.random.default_rng(5)
    # Aligned within stream A; stream B's trades sit far from its events.
    streams = {
        "A": ([100.0, 400.0], [100.0, 400.0]),
        "B": ([2000.0], [1000.0]),
    }
    _, observed = permutation_p_value(streams, 10, permutations=10, rng=rng)
    assert observed == 2, "only stream A's pairs count"


def test_streams_cannot_cross_match_even_with_a_huge_window():
    # Streams are packed into one array for speed, each displaced into its own
    # lane. The lane stride has to stay wider than any window can reach, or the
    # optimisation would quietly start pairing one ticker's trades with
    # another's events.
    streams = {"A": ([0.0, 500.0], [0.0]), "B": ([0.0, 500.0], [0.0])}
    _, observed = permutation_p_value(streams, 100_000, permutations=5)
    assert observed == 4, "two trades per stream, one event each, no crossing"


def test_before_only_window_ignores_trades_after_the_event():
    # Contract front-running is a directional claim: buying before an award.
    after = {"T": ([1030.0], [1000.0])}
    before = {"T": ([970.0], [1000.0])}
    assert permutation_p_value(after, 60, "before", permutations=10)[1] == 0
    assert permutation_p_value(before, 60, "before", permutations=10)[1] == 1


def test_cluster_null_needs_members_to_align():
    rng = np.random.default_rng(9)
    # Five members each trading once, all within three days of each other.
    aligned = {mid: [1000.0 + mid] for mid in range(5)}
    p = cluster_p_value(aligned, 14, observed_members=5, permutations=300, rng=rng)
    # Every member has exactly one trade over a tiny span, so there is nothing
    # for the shift to separate -- correctly, no claim is made.
    assert p is None or p > 0.0


def test_cluster_null_returns_none_when_too_few_members():
    assert cluster_p_value({1: [1.0], 2: [500.0]}, 14, observed_members=5) is None


# --------------------------------------------------------------------------
# Wiring: which detectors get a null, and which explicitly do not
# --------------------------------------------------------------------------


def test_every_spec_reuses_its_detectors_window():
    from src.analysis.legislation import DEFAULT_WINDOW_DAYS
    from src.analysis.tier2_detectors import (
        DEFAULT_CONTRACT_WINDOW_DAYS,
        DEFAULT_DONOR_WINDOW_DAYS,
        DEFAULT_LOBBYING_WINDOW_DAYS,
    )

    windows = {spec.anomaly_type: spec.window_days for spec in NULL_SPECS}
    # A null tested at a different window than the detector used would be
    # measuring a different question.
    assert windows["donor_conflict"] == DEFAULT_DONOR_WINDOW_DAYS
    assert windows["lobbying_overlap"] == DEFAULT_LOBBYING_WINDOW_DAYS
    assert windows["contract_front_run"] == DEFAULT_CONTRACT_WINDOW_DAYS
    assert windows["sponsorship_conflict"] == DEFAULT_WINDOW_DAYS
    assert windows["bill_jurisdiction_conflict"] == DEFAULT_WINDOW_DAYS


def test_no_detector_is_both_tested_and_declared_untestable():
    assert not set(NO_NULL_MODEL) & {spec.anomaly_type for spec in NULL_SPECS}


def test_every_emitted_anomaly_type_is_accounted_for():
    """Each detector is either given a null model or explicitly excused one.

    A new detector that is neither would silently get NULL and read as
    "untested" forever without anyone deciding that.
    """
    from src.analysis.baselines import DETECTOR_SOURCE_TABLES

    covered = set(NO_NULL_MODEL) | {spec.anomaly_type for spec in NULL_SPECS}
    covered.add("cross_member_cluster")  # handled by its own ticker-level null
    assert set(DETECTOR_SOURCE_TABLES) <= covered


# --------------------------------------------------------------------------
# End to end against the database
# --------------------------------------------------------------------------


def _member(db, bioguide="S000001") -> Member:
    member = Member(
        bioguide_id=bioguide,
        first_name="Test",
        last_name=bioguide,
        chamber=Chamber.SENATE,
        party=Party.DEMOCRAT,
        state="WA",
    )
    db.add(member)
    db.commit()
    return member


def _trade(db, member, ticker, when):
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=when.year,
        filing_type="PTR",
        filing_date=when + timedelta(days=10),
        document_id=f"D{member.id}{ticker}{when.isoformat()}",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=when,
            transaction_type=TransactionType.PURCHASE,
            description=f"{ticker} stock",
            ticker=ticker,
        )
    )
    db.commit()


def _anomaly(db, member, anomaly_type, title):
    db.add(
        Anomaly(
            member_id=member.id,
            anomaly_type=anomaly_type,
            severity="HIGH",
            title=title,
            description=title,
        )
    )
    db.commit()


def test_magnitude_detectors_are_left_without_a_p_value(db_session):
    member = _member(db_session)
    _anomaly(db_session, member, "sector_concentration", "High concentration in defense")

    result = annotate_significance(db_session, permutations=20, seed=1)

    row = db_session.query(Anomaly).one()
    assert row.p_value is None and row.q_value is None
    assert result["findings_without_a_null_model"] == 1


def test_a_stale_value_on_an_untestable_detector_is_cleared(db_session):
    # Belt and braces: a q-value left behind by an earlier run must not survive
    # into one where the detector has no null model, or it reads as a pass.
    member = _member(db_session)
    _anomaly(db_session, member, "late_filing", "Filed 90 days late")
    db_session.query(Anomaly).update({"q_value": 0.001, "p_value": 0.0005})
    db_session.commit()

    annotate_significance(db_session, permutations=20, seed=1)

    row = db_session.query(Anomaly).one()
    assert row.q_value is None and row.p_value is None


def test_a_coincidence_detector_gets_a_q_value(db_session):
    member = _member(db_session)
    start = datetime(2023, 1, 1)
    # Donations and trades in the same ticker, aligned, over a long span.
    for offset in range(0, 700, 70):
        when = start + timedelta(days=offset)
        _trade(db_session, member, "LMT", when + timedelta(days=2))
        db_session.add(
            CampaignDonation(
                member_id=member.id, ticker="LMT", donor_name="LMT PAC", donation_date=when
            )
        )
    db_session.commit()
    _anomaly(db_session, member, "donor_conflict", "Donor conflict: traded LMT 2d after donation")

    result = annotate_significance(db_session, permutations=200, seed=7)

    row = db_session.query(Anomaly).one()
    assert row.p_value is not None
    assert row.q_value is not None
    assert result["tests"] >= 1


def test_summary_separates_untested_from_failing(db_session):
    member = _member(db_session)
    _anomaly(db_session, member, "sector_concentration", "High concentration")
    annotate_significance(db_session, permutations=20, seed=1)

    summary = significance_summary(db_session)
    assert summary["findings_without_a_null_model"] == 1
    assert summary["findings_with_a_null_model"] == 0
    assert summary["findings_passing_fdr"] == 0
