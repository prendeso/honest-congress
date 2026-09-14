"""How often chance alone would produce a finding this strong.

Seventeen detectors run against every member. At any realistic false-positive
rate that names people who did nothing, and this project publishes their names,
so the suite needs to say which findings survive the fact that it looked
everywhere.

The obstacle is that no detector produces a p-value. Every one is a threshold
rule -- `>50%` concentration, `within 60 days`, `5+ consecutive trades` -- so
there is nothing for Benjamini-Hochberg to rank. A null model has to exist
first, and one only exists for some of them.

Where a null exists
-------------------
Six detectors ask a *timing* question: did this member trade near these events?
That has a well-posed null -- the same trades, the same events, no relationship
between them -- and :func:`permutation_p_value` measures it directly.

The other ten ask about magnitude ("concentration above 50%", "filed 90 days
late") or compound other detectors. There is no coincidence to destroy and no
defensible null to shuffle, so they get ``q_value = None``. NULL means *no null
model*, never *passed*. They keep `percentile_rank`, which is the right tool for
a magnitude.

Why shift rather than resample
------------------------------
Disclosed trades arrive in same-day batches: one PTR carries a dozen
transactions dated together. A null that resampled dates independently would
scatter those batches, make clustered coincidences look rare, and manufacture
significance on exactly the data this project is built from.

So the null *shifts* the member's entire trading calendar by one random offset
and re-counts. Batches stay intact, the member's own trading rhythm stays
intact, and only the alignment with the events is destroyed -- which is the
single thing being tested.

What the unit of test is
------------------------
One test per (member, detector), not one per finding. The question a reader has
is "does this member's trading line up with these events more than chance?",
asked once. Every finding for that pair inherits its q-value, so a member with
one striking coincidence among two hundred trades comes out weak -- which is
what multiple-comparisons control is for, not a flaw in it.

What surviving this does not mean
---------------------------------
That the alignment is unlikely by chance. Not that the member acted on
anything, not that the bill or the donation caused the trade, and not that the
detector's threshold is calibrated. It is one specific claim, and the API
reports it as one.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np
from sqlalchemy.orm import Session

from src.analysis.legislation import DEFAULT_WINDOW_DAYS as LEGISLATION_WINDOW_DAYS
from src.analysis.sectors import SectorIndex, policy_area_sectors
from src.analysis.tier2_detectors import (
    DEFAULT_CONTRACT_WINDOW_DAYS,
    DEFAULT_DONOR_WINDOW_DAYS,
    DEFAULT_LOBBYING_WINDOW_DAYS,
    award_action_criteria,
)
from src.db.models import (
    Anomaly,
    Bill,
    BillCommittee,
    BillSponsorship,
    CampaignDonation,
    CommitteeAssignment,
    Disclosure,
    GovernmentContract,
    LobbyingDisclosure,
    Transaction,
    TransactionType,
)

logger = logging.getLogger(__name__)

DEFAULT_PERMUTATIONS = 1000
DEFAULT_FDR_ALPHA = 0.05

# A member whose trades and events span fewer days than this cannot be shifted
# into a meaningfully different alignment, so no p-value is claimed for them.
MIN_SPAN_DAYS = 30

# Detectors whose finding is a magnitude or a compound of other detectors. There
# is no coincidence to destroy, so they are left unmeasured rather than given a
# fabricated number. Listed explicitly so the omission is a decision on the
# record rather than an oversight.
NO_NULL_MODEL = (
    "sector_concentration",
    "late_filing",
    "large_trade",
    "volume_spikes",
    "high_trading_frequency",
    "trade_clustering",
    "wealth_vs_salary",
    "excessive_wealth_growth",
    "rapid_asset_appreciation",
    "committee_jurisdiction_conflict",
    "multi_factor_risk",
)


# A member's trades and the events they are being tested against, grouped by the
# stream they have to match within -- a ticker, or a sector. Counting across
# streams would match an Apple trade to a Boeing donation.
Streams = Dict[str, Tuple[List[float], List[float]]]


@dataclass
class NullSpec:
    """How to build the null for one detector."""

    anomaly_type: str
    window_days: int
    # "symmetric" counts trades either side of the event; "before" counts only
    # trades in the window preceding it, which is what contract front-running
    # means and the only asymmetric case here.
    direction: str
    collect: Callable[[Session], Dict[int, Streams]]
    description: str = field(default="")


def _days(value: datetime) -> float:
    return value.toordinal()


def benjamini_hochberg(p_values: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg q-values, in the input's order.

    The step that a first attempt usually gets wrong is the enforced
    monotonicity: scanning from the largest p downwards and carrying the running
    minimum. Without it a q-value can exceed the q-value of a *less* significant
    finding, which is incoherent -- and the naive `p * n / rank` alone does
    exactly that on real data.
    """
    n = len(p_values)
    if n == 0:
        return []

    order = sorted(range(n), key=lambda i: p_values[i])
    q = [0.0] * n
    running_min = 1.0
    for rank in range(n, 0, -1):
        index = order[rank - 1]
        value = p_values[index] * n / rank
        running_min = min(running_min, value)
        q[index] = min(1.0, running_min)
    return q


def permutation_p_value(
    streams: Streams,
    window_days: int,
    direction: str = "symmetric",
    permutations: int = DEFAULT_PERMUTATIONS,
    rng: np.random.Generator | None = None,
) -> Tuple[float | None, int]:
    """Probability of this many coincidences under a shifted-calendar null.

    Returns ``(p_value, observed_count)``. `p_value` is None when the data
    cannot support one: nothing to shift, or a span too short for a shift to
    mean anything.

    One offset is drawn per permutation and applied to *every* stream at once,
    because the thing being tested is the member's whole trading calendar
    against the whole event calendar -- not each ticker independently.

    The streams are packed into a single array, each displaced into its own
    "lane" far enough apart that no window can reach across one. That keeps a
    Boeing trade from pairing with an Apple donation while letting each
    permutation be three vectorised operations instead of three per ticker --
    the difference between a nightly job of about a minute and one of an hour.
    """
    rng = rng or np.random.default_rng()

    trade_lists = []
    event_lists = []
    for trades, events in streams.values():
        if trades and events:
            trade_lists.append(np.asarray(trades, dtype=float))
            event_lists.append(np.asarray(events, dtype=float))

    if not trade_lists:
        return None, 0

    everything = np.concatenate(trade_lists + event_lists)
    origin = float(everything.min())
    span = float(everything.max() - everything.min())

    observed = _count_packed(
        _pack(trade_lists, origin, span, window_days),
        _pack(event_lists, origin, span, window_days),
        window_days,
        direction,
    )
    if span < MIN_SPAN_DAYS:
        return None, observed

    lane = _lane_offsets(trade_lists, span, window_days)
    local = np.concatenate([arr - origin for arr in trade_lists])
    events_packed = _pack(event_lists, origin, span, window_days)

    at_least_as_extreme = 0
    for offset in rng.uniform(0.0, span, size=permutations):
        shifted = np.sort(np.mod(local + offset, span) + lane)
        if _count_packed(shifted, events_packed, window_days, direction) >= observed:
            at_least_as_extreme += 1

    # The +1s are Phipson & Smyth: a permutation p-value of exactly zero claims
    # more than the simulation can support, and 1/(N+1) is the floor it earns.
    p_value = (1.0 + at_least_as_extreme) / (1.0 + permutations)
    return p_value, observed


def _lane_stride(span: float, window_days: int) -> float:
    """Gap between packed streams, wide enough that no window spans two."""
    return span + 2.0 * window_days + 1.0


def _lane_offsets(arrays: List[np.ndarray], span: float, window_days: int) -> np.ndarray:
    stride = _lane_stride(span, window_days)
    return np.concatenate([np.full(arr.size, index * stride) for index, arr in enumerate(arrays)])


def _pack(arrays: List[np.ndarray], origin: float, span: float, window_days: int) -> np.ndarray:
    """Flatten per-stream dates into one sorted array, each stream in its own lane."""
    stride = _lane_stride(span, window_days)
    return np.sort(
        np.concatenate([arr - origin + index * stride for index, arr in enumerate(arrays)])
    )


def _count_packed(trades: np.ndarray, events: np.ndarray, window_days: int, direction: str) -> int:
    """(event, trade) pairs inside the window -- what the detector itself counts."""
    if trades.size == 0 or events.size == 0:
        return 0
    if direction == "before":
        # Trades in [event - window, event]: the front-running case.
        lo = np.searchsorted(trades, events - window_days, side="left")
        hi = np.searchsorted(trades, events, side="right")
    else:
        lo = np.searchsorted(trades, events - window_days, side="left")
        hi = np.searchsorted(trades, events + window_days, side="right")
    return int(np.sum(hi - lo))


def _shift(dates: np.ndarray, offset: float, origin: float, span: float) -> np.ndarray:
    """Circular shift within the observed span, batches intact.

    Every date moves by the same offset and wraps, so same-day clusters stay
    same-day and the member's trading rhythm survives. Only the alignment with
    the events is destroyed.
    """
    return origin + np.mod(dates - origin + offset, span)


# ---------------------------------------------------------------------------
# Collectors: what each detector's trades and events actually are.
#
# Each returns member_id -> stream -> (trade dates, event dates). The stream key
# is whatever the detector requires a match within -- a ticker for the Tier-2
# detectors, a sector for the legislative ones -- because counting across
# streams would pair an Apple trade with a Boeing donation.
# ---------------------------------------------------------------------------


def _member_trades(db: Session) -> Dict[int, List[Tuple[str, float, bool]]]:
    """Every disclosed trade as (ticker, ordinal date, is_purchase), by member."""
    rows = (
        db.query(
            Disclosure.member_id,
            Transaction.ticker,
            Transaction.transaction_date,
            Transaction.transaction_type,
        )
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Transaction.transaction_date.isnot(None))
        .all()
    )
    by_member: Dict[int, List[Tuple[str, float, bool]]] = defaultdict(list)
    for member_id, ticker, when, kind in rows:
        if member_id is None or when is None:
            continue
        by_member[member_id].append(
            ((ticker or "").strip().upper(), _days(when), kind == TransactionType.PURCHASE)
        )
    return by_member


def _collect_donor(db: Session) -> Dict[int, Streams]:
    """Donations to a member from a company, against that member's trades in it."""
    trades = _member_trades(db)
    streams: Dict[int, Streams] = defaultdict(dict)

    events: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for member_id, ticker, when in (
        db.query(
            CampaignDonation.member_id, CampaignDonation.ticker, CampaignDonation.donation_date
        )
        .filter(CampaignDonation.ticker.isnot(None))
        .filter(CampaignDonation.donation_date.isnot(None))
        .all()
    ):
        events[member_id][(ticker or "").strip().upper()].append(_days(when))

    for member_id, by_ticker in events.items():
        member_trades: Dict[str, List[float]] = defaultdict(list)
        for ticker, when, _ in trades.get(member_id, []):
            member_trades[ticker].append(when)
        for ticker, dates in by_ticker.items():
            if member_trades.get(ticker):
                streams[member_id][ticker] = (member_trades[ticker], dates)
    return streams


def _collect_lobbying(db: Session) -> Dict[int, Streams]:
    """Lobbying filings are market-wide, so every member trading the issuer is in scope."""
    trades = _member_trades(db)

    filings: Dict[str, List[float]] = defaultdict(list)
    for ticker, when in (
        db.query(LobbyingDisclosure.ticker, LobbyingDisclosure.filed_date)
        .filter(LobbyingDisclosure.filed_date.isnot(None))
        .all()
    ):
        filings[(ticker or "").strip().upper()].append(_days(when))

    streams: Dict[int, Streams] = defaultdict(dict)
    for member_id, member_trades in trades.items():
        by_ticker: Dict[str, List[float]] = defaultdict(list)
        for ticker, when, _ in member_trades:
            if ticker in filings:
                by_ticker[ticker].append(when)
        for ticker, dates in by_ticker.items():
            streams[member_id][ticker] = (dates, filings[ticker])
    return streams


def _collect_contracts(db: Session) -> Dict[int, Streams]:
    """Purchases only: the detector's claim is about buying *before* an award.

    Filtered by :func:`award_action_criteria`, the same call the detector makes,
    so the events the null model shuffles are exactly the events the findings
    were drawn from. A deobligation counted here but not there would deflate
    every contract q-value by padding the event stream with dates no finding
    could ever have come from.
    """
    trades = _member_trades(db)

    awards: Dict[str, List[float]] = defaultdict(list)
    for ticker, when in (
        db.query(GovernmentContract.ticker, GovernmentContract.awarded_date)
        .filter(*award_action_criteria())
        .all()
    ):
        awards[(ticker or "").strip().upper()].append(_days(when))

    streams: Dict[int, Streams] = defaultdict(dict)
    for member_id, member_trades in trades.items():
        by_ticker: Dict[str, List[float]] = defaultdict(list)
        for ticker, when, is_purchase in member_trades:
            if is_purchase and ticker in awards:
                by_ticker[ticker].append(when)
        for ticker, dates in by_ticker.items():
            streams[member_id][ticker] = (dates, awards[ticker])
    return streams


def _sector_trades(db: Session) -> Dict[int, Dict[str, List[float]]]:
    """A member's trades grouped by sector, using the same index the detectors use."""
    index = SectorIndex.from_db(db)
    rows = (
        db.query(
            Disclosure.member_id,
            Transaction.ticker,
            Transaction.description,
            Transaction.transaction_date,
        )
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Transaction.transaction_date.isnot(None))
        .all()
    )
    by_member: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for member_id, ticker, description, when in rows:
        if member_id is None or when is None:
            continue
        for sector in index.classify(ticker, description):
            by_member[member_id][sector].append(_days(when))
    return by_member


def _collect_sponsorship(db: Session) -> Dict[int, Streams]:
    """Bills the member sponsored, against their trades in the affected sector."""
    sector_trades = _sector_trades(db)

    events: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for member_id, policy_area, introduced in (
        db.query(BillSponsorship.member_id, Bill.policy_area, Bill.introduced_date)
        .join(Bill, BillSponsorship.bill_id == Bill.id)
        .filter(BillSponsorship.is_sponsor.is_(True))
        .filter(Bill.policy_area.isnot(None))
        .filter(Bill.introduced_date.isnot(None))
        .all()
    ):
        for sector in policy_area_sectors(policy_area):
            events[member_id][sector].append(_days(introduced))

    streams: Dict[int, Streams] = defaultdict(dict)
    for member_id, by_sector in events.items():
        for sector, dates in by_sector.items():
            trades = sector_trades.get(member_id, {}).get(sector)
            if trades:
                streams[member_id][sector] = (trades, dates)
    return streams


def _collect_bill_jurisdiction(db: Session) -> Dict[int, Streams]:
    """Bills reaching a committee the member sits on, against their sector trades."""
    from src.analysis.legislation import _parent_committee

    sector_trades = _sector_trades(db)

    seats: Dict[int, set[str]] = defaultdict(set)
    for member_id, committee_id in db.query(
        CommitteeAssignment.member_id, CommitteeAssignment.committee_id
    ).all():
        parent = _parent_committee(committee_id)
        if parent:
            seats[member_id].add(parent)

    referrals: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for committee_id, policy_area, when in (
        db.query(BillCommittee.committee_id, Bill.policy_area, BillCommittee.activity_date)
        .join(Bill, BillCommittee.bill_id == Bill.id)
        .filter(BillCommittee.activity_date.isnot(None))
        .filter(Bill.policy_area.isnot(None))
        .all()
    ):
        parent = _parent_committee(committee_id)
        if not parent:
            continue
        for sector in policy_area_sectors(policy_area):
            referrals[parent][sector].append(_days(when))

    streams: Dict[int, Streams] = defaultdict(dict)
    for member_id, committees in seats.items():
        for committee in committees:
            for sector, dates in referrals.get(committee, {}).items():
                trades = sector_trades.get(member_id, {}).get(sector)
                if not trades:
                    continue
                key = f"{committee}:{sector}"
                streams[member_id][key] = (trades, dates)
    return streams


NULL_SPECS: Tuple[NullSpec, ...] = (
    NullSpec(
        anomaly_type="donor_conflict",
        window_days=DEFAULT_DONOR_WINDOW_DAYS,
        direction="symmetric",
        collect=_collect_donor,
        description="trades in a donor's stock, against donations from that donor",
    ),
    NullSpec(
        anomaly_type="lobbying_overlap",
        window_days=DEFAULT_LOBBYING_WINDOW_DAYS,
        direction="symmetric",
        collect=_collect_lobbying,
        description="trades in an issuer, against that issuer's lobbying filings",
    ),
    NullSpec(
        anomaly_type="contract_front_run",
        window_days=DEFAULT_CONTRACT_WINDOW_DAYS,
        direction="before",
        collect=_collect_contracts,
        description="purchases, against federal awards to the same issuer",
    ),
    NullSpec(
        anomaly_type="sponsorship_conflict",
        window_days=LEGISLATION_WINDOW_DAYS,
        direction="symmetric",
        collect=_collect_sponsorship,
        description="sector trades, against bills the member sponsored",
    ),
    NullSpec(
        anomaly_type="bill_jurisdiction_conflict",
        window_days=LEGISLATION_WINDOW_DAYS,
        direction="symmetric",
        collect=_collect_bill_jurisdiction,
        description="sector trades, against bills reaching the member's committees",
    ),
)


def cluster_p_value(
    member_dates: Dict[int, List[float]],
    window_days: int,
    observed_members: int,
    permutations: int = DEFAULT_PERMUTATIONS,
    rng: np.random.Generator | None = None,
) -> float | None:
    """How often this many members coincide in one window by chance.

    `cross_member_cluster` is the one detector whose finding belongs to a ticker
    rather than a member, so its null differs from the rest: each member's trades
    in the ticker are shifted *independently*. That leaves every member's own
    trading pattern intact and destroys only the alignment between them, which is
    the entire claim being tested.
    """
    rng = rng or np.random.default_rng()

    series = {
        mid: np.asarray(sorted(dates), dtype=float) for mid, dates in member_dates.items() if dates
    }
    if len(series) < observed_members:
        return None

    everything = np.concatenate(list(series.values()))
    span = float(everything.max() - everything.min())
    origin = float(everything.min())
    if span < MIN_SPAN_DAYS:
        return None

    at_least_as_extreme = 0
    for _ in range(permutations):
        marks: List[Tuple[float, int]] = []
        for member_id, dates in series.items():
            shifted = _shift(dates, float(rng.uniform(0.0, span)), origin, span)
            marks.extend((float(d), member_id) for d in shifted)
        if _max_members_in_window(marks, window_days) >= observed_members:
            at_least_as_extreme += 1

    return (1.0 + at_least_as_extreme) / (1.0 + permutations)


def _max_members_in_window(marks: List[Tuple[float, int]], window_days: int) -> int:
    """Most distinct members inside any window -- the detector's own statistic."""
    if not marks:
        return 0
    marks.sort()
    best = 0
    start = 0
    counts: Dict[int, int] = defaultdict(int)
    distinct = 0
    for end in range(len(marks)):
        member_id = marks[end][1]
        counts[member_id] += 1
        if counts[member_id] == 1:
            distinct += 1
        while marks[end][0] - marks[start][0] > window_days:
            leaving = marks[start][1]
            counts[leaving] -= 1
            if counts[leaving] == 0:
                distinct -= 1
            start += 1
        best = max(best, distinct)
    return best


def _cluster_p_values(
    db: Session, permutations: int, rng: np.random.Generator
) -> Dict[Tuple[str, str], float]:
    """A p-value per (ticker, direction) the cluster detector flagged."""
    from src.analysis.clustering import CLUSTER_WINDOW_DAYS

    rows = (
        db.query(
            Disclosure.member_id,
            Transaction.ticker,
            Transaction.transaction_date,
            Transaction.transaction_type,
        )
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Transaction.ticker.isnot(None))
        .filter(Transaction.transaction_date.isnot(None))
        .all()
    )

    grouped: Dict[Tuple[str, str], Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for member_id, ticker, when, kind in rows:
        if member_id is None or kind not in (TransactionType.PURCHASE, TransactionType.SALE):
            continue
        direction = "purchase" if kind == TransactionType.PURCHASE else "sale"
        grouped[((ticker or "").strip().upper(), direction)][member_id].append(_days(when))

    results: Dict[Tuple[str, str], float] = {}
    for key, member_dates in grouped.items():
        marks = [(when, mid) for mid, dates in member_dates.items() for when in dates]
        observed = _max_members_in_window(marks, CLUSTER_WINDOW_DAYS)
        p_value = cluster_p_value(
            member_dates, CLUSTER_WINDOW_DAYS, observed, permutations=permutations, rng=rng
        )
        if p_value is not None:
            results[key] = p_value
    return results


def annotate_significance(
    db: Session,
    permutations: int = DEFAULT_PERMUTATIONS,
    alpha: float = DEFAULT_FDR_ALPHA,
    seed: int | None = None,
) -> Dict[str, Any]:
    """Attach p-values and Benjamini-Hochberg q-values to every testable finding.

    One test per (member, detector), plus one per flagged (ticker, direction)
    cluster. Findings belonging to a tested pair inherit its q-value; findings
    from a detector with no null model are explicitly set back to NULL, so a
    stale value can never masquerade as a result.
    """
    rng = np.random.default_rng(seed)

    # (anomaly_type, member_id) -> p, and ("cross_member_cluster", ticker) -> p.
    tests: List[Tuple[str, int | None, str | None, float]] = []

    for spec in NULL_SPECS:
        for member_id, streams in spec.collect(db).items():
            p_value, observed = permutation_p_value(
                streams,
                spec.window_days,
                spec.direction,
                permutations=permutations,
                rng=rng,
            )
            if p_value is not None and observed > 0:
                tests.append((spec.anomaly_type, member_id, None, p_value))

    for (cluster_ticker, _direction), cluster_p in _cluster_p_values(db, permutations, rng).items():
        tests.append(("cross_member_cluster", None, cluster_ticker, cluster_p))

    q_values = benjamini_hochberg([p for _, _, _, p in tests])

    by_member: Dict[Tuple[str, int], Tuple[float, float]] = {}
    by_ticker: Dict[str, Tuple[float, float]] = {}
    for (tested_type, tested_member, tested_ticker, tested_p), q_value in zip(
        tests, q_values, strict=True
    ):
        if tested_member is not None:
            by_member[(tested_type, tested_member)] = (tested_p, q_value)
        elif tested_ticker is not None:
            # A ticker can be flagged in both directions; keep the stronger.
            existing = by_ticker.get(tested_ticker)
            if existing is None or tested_p < existing[0]:
                by_ticker[tested_ticker] = (tested_p, q_value)

    annotated = 0
    without_null_model = 0
    for anomaly in db.query(Anomaly).all():
        found = None
        if anomaly.anomaly_type not in NO_NULL_MODEL:
            found = by_member.get((anomaly.anomaly_type, anomaly.member_id))
            if found is None and anomaly.anomaly_type == "cross_member_cluster":
                found = _cluster_match(anomaly, by_ticker)

        if found is None:
            # Either a magnitude rule with no null to shuffle, or testable in
            # principle but not in this run -- too short a span, no eligible
            # trades. Both are "untested"; neither is "passed". Assigned
            # unconditionally so a value left by an earlier run cannot survive
            # into one where it no longer holds.
            anomaly.p_value = anomaly.q_value = None
            without_null_model += 1
            continue

        anomaly.p_value, anomaly.q_value = found
        annotated += 1

    db.commit()

    passing = sum(1 for _, q in by_member.values() if q <= alpha) + sum(
        1 for _, q in by_ticker.values() if q <= alpha
    )
    logger.info(
        "Significance: %d tests, %d passing FDR at alpha=%.3f; "
        "%d findings annotated, %d left without a null model",
        len(tests),
        passing,
        alpha,
        annotated,
        without_null_model,
    )
    return {
        "tests": len(tests),
        "tests_passing_fdr": passing,
        "alpha": alpha,
        "permutations": permutations,
        "findings_annotated": annotated,
        "findings_without_a_null_model": without_null_model,
        # What an alpha-level FDR says you should expect to be wrong among the
        # findings that passed. Reported rather than left for the reader to work
        # out, because that is the number the caveat is actually about.
        "expected_false_discoveries": round(passing * alpha, 2),
    }


def _cluster_match(
    anomaly: Anomaly, by_ticker: Dict[str, Tuple[float, float]]
) -> Tuple[float, float] | None:
    """Find a cluster finding's ticker in its title.

    Cluster anomalies are attributed to each participating member, so they carry
    no ticker column -- the ticker is named in the title the detector wrote.
    """
    title = (anomaly.title or "").upper()
    for ticker, values in by_ticker.items():
        if ticker and f" {ticker} " in f" {title} ":
            return values
    return None
