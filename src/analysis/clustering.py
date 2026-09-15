"""Several members trading the same issuer at the same time.

Individual timing is hard to say anything defensible about: disclosures give
amount bands and no share counts, and without price history there is no return
to measure. Co-movement across members sidesteps that. It needs no prices, no
position sizes and no assumption about intent -- only dates, tickers and
directions, all of which are disclosed exactly.

It is also the more interesting question. One member buying a defense stock is
unremarkable; eleven buying it the same week is a pattern worth showing a
reader.

The honest difficulty is the base rate. Members trade popular large-caps
constantly, so "five members bought NVDA this month" may be nothing at all.
The filter is *concentration*, not raw popularity: what share of everyone who
ever traded this ticker did so inside this one window. Four of a ticker's forty
traders happening to overlap is noise; six of its six traders moving together is
a burst. Suppressing on popularity alone would be backwards -- in any population
the largest and most interesting clusters also involve the most members.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from src.db.models import Disclosure, Member, Transaction, TransactionType

logger = logging.getLogger(__name__)

ANOMALY_TYPE = "cross_member_cluster"

# Distinct members trading the same ticker, same direction, inside the window.
MIN_MEMBERS_IN_CLUSTER = 4
CLUSTER_WINDOW_DAYS = 14

# Share of a ticker's lifetime traders that must fall inside the window. Below
# this, the overlap is better explained by the ticker being widely held than by
# anything about the timing.
MIN_CLUSTER_CONCENTRATION = 0.5


# "purchase" and "sale" are the stored directions; neither takes a "d".
_PAST_TENSE = {"purchase": "bought", "sale": "sold"}


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _cluster_key(txn: Transaction) -> Tuple[str, str] | None:
    if not txn.ticker or not txn.transaction_date:
        return None
    if txn.transaction_type == TransactionType.PURCHASE:
        direction = "purchase"
    elif txn.transaction_type == TransactionType.SALE:
        direction = "sale"
    else:
        return None
    return (txn.ticker.strip().upper(), direction)


def detect_cross_member_clusters(db: Session) -> List[Dict[str, Any]]:
    """Find tickers several members traded the same way at the same time."""
    rows = (
        db.query(Transaction, Disclosure.member_id)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Transaction.ticker.isnot(None))
        .all()
    )
    if not rows:
        return []

    # (ticker, direction) -> [(date, member_id)]
    events: Dict[Tuple[str, str], List[Tuple[Any, int]]] = defaultdict(list)
    # ticker -> members who traded it at all, for the ubiquity check
    ticker_members: Dict[str, set] = defaultdict(set)
    trading_members: set = set()

    for txn, member_id in rows:
        key = _cluster_key(txn)
        if key is None or member_id is None:
            continue
        events[key].append((txn.transaction_date, member_id))
        ticker_members[key[0]].add(member_id)
        trading_members.add(member_id)

    if not trading_members:
        return []

    member_names = {m.id: f"{m.first_name} {m.last_name}" for m in db.query(Member).all()}
    total_traders = len(trading_members)

    anomalies: List[Dict[str, Any]] = []
    window = timedelta(days=CLUSTER_WINDOW_DAYS)

    for (ticker, direction), occurrences in events.items():
        ubiquity = len(ticker_members[ticker]) / total_traders
        occurrences.sort(key=lambda pair: pair[0])

        # Sliding window over trade dates; a member is counted once per cluster.
        start = 0
        best: Dict[str, Any] | None = None
        for end in range(len(occurrences)):
            while occurrences[end][0] - occurrences[start][0] > window:
                start += 1

            members_in_window = {mid for _, mid in occurrences[start : end + 1]}
            if len(members_in_window) < MIN_MEMBERS_IN_CLUSTER:
                continue

            if best is None or len(members_in_window) > best["member_count"]:
                best = {
                    "member_count": len(members_in_window),
                    "member_ids": sorted(members_in_window),
                    "window_start": occurrences[start][0],
                    "window_end": occurrences[end][0],
                }

        if best is None:
            continue

        # How much of this ticker's entire trading history sits inside the window.
        concentration = best["member_count"] / len(ticker_members[ticker])
        if concentration < MIN_CLUSTER_CONCENTRATION:
            continue

        names = sorted(member_names.get(mid, f"member {mid}") for mid in best["member_ids"])
        span_days = (best["window_end"] - best["window_start"]).days

        anomalies.append(
            {
                # Attributed to each participant so it surfaces on their page;
                # the finding itself is about the group.
                "member_id": best["member_ids"][0],
                "member_name": names[0],
                "anomaly_type": ANOMALY_TYPE,
                "severity": "HIGH" if best["member_count"] >= 6 else "MEDIUM",
                # `f"{direction}d"` coined a verb: direction is "purchase" or
                # "sale", so every sale cluster was titled "4 members saled NVDA
                # within 1 days" -- in a HIGH-severity title that names the
                # members. The description one line below has always read
                # correctly ("disclosed a sale of"), which is what makes the
                # title a slip rather than a choice.
                "title": (
                    f"{best['member_count']} members {_PAST_TENSE[direction]} {ticker} "
                    f"within {_days(max(span_days, 1))}"
                ),
                "ticker": ticker,
                "direction": direction,
                "member_count": best["member_count"],
                "member_ids": best["member_ids"],
                "member_names": names,
                "window_start": best["window_start"].date().isoformat(),
                "window_end": best["window_end"].date().isoformat(),
                "window_days": span_days,
                "ticker_ubiquity_percent": round(ubiquity * 100, 2),
                "cluster_concentration_percent": round(concentration * 100, 2),
                "computed_value": Decimal(str(best["member_count"])),
                "threshold_value": Decimal(str(MIN_MEMBERS_IN_CLUSTER)),
                "description": (
                    f"{best['member_count']} members disclosed a {direction} of {ticker} "
                    f"between {best['window_start'].date()} and {best['window_end'].date()} "
                    f"({', '.join(names)}). "
                    f"That is {round(concentration * 100)}% of everyone who has ever "
                    f"traded {ticker} here, and {round(ubiquity * 100)}% of trading "
                    f"members hold it at all. "
                    f"Filing dates are not trade dates, and disclosure lags "
                    f"vary by member; this shows co-movement in reported activity, not "
                    f"coordination."
                ),
            }
        )

    anomalies.sort(key=lambda a: -a["member_count"])
    logger.info("Cross-member clusters: %d findings", len(anomalies))
    return anomalies


def run_cluster_detection(db: Session, persist: bool = True) -> Dict[str, Any]:
    """Run clustering and optionally persist the findings."""
    from src.analysis import persist_anomalies

    anomalies = detect_cross_member_clusters(db)
    persisted = persist_anomalies(db, anomalies) if persist else 0

    return {"anomalies": anomalies, "total": len(anomalies), "persisted": persisted}
