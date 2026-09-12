"""STOCK Act filing compliance, per member.

The STOCK Act requires a Periodic Transaction Report within 30 days of a member
becoming aware of a covered transaction, and no later than 45 days after the
transaction itself. The 45-day figure is the one that can be checked from the
filings alone: awareness dates are not disclosed, so 45 days is the only
deadline the public record supports.

This is deliberately the least interpretive thing the project computes. It
makes no claim about intent, timing, profit or conflict -- it is subtraction
between two dates that both appear on the filing. That is exactly why it is
worth publishing: it is the one output nobody can argue with, and no competitor
publishes it rigorously.

It differs from the `late_filing` detector in `trade_analyzer`, which flags
individual materially-late trades above a dollar threshold to keep the anomaly
table readable. A compliance *rate* must count every covered transaction,
including the small ones, or it is not a rate.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.db.models import Disclosure, Member, Transaction

logger = logging.getLogger(__name__)

# 45 days after the transaction. See module docstring on why not 30.
PTR_DEADLINE_DAYS = 45


def _median(values: List[int]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def member_compliance(db: Session, member: Member) -> Dict[str, Any] | None:
    """Filing punctuality for one member, or None if nothing is checkable.

    A transaction is only checkable when both its own date and the filing date
    of its disclosure are known. Both columns are currently NOT NULL, so the
    guard below is defensive rather than reachable -- but if either is ever
    relaxed, an unknown date must drop out of the denominator rather than be
    silently counted as filed on time, which would understate the late rate.
    """
    rows = (
        db.query(Transaction, Disclosure)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(
            Disclosure.member_id == member.id,
            Disclosure.is_ptr.is_(True),
        )
        .all()
    )

    days_late: List[int] = []
    checkable = 0
    late_value = Decimal(0)
    worst: Dict[str, Any] | None = None

    for txn, disclosure in rows:
        if not txn.transaction_date or not disclosure.filing_date:
            continue

        checkable += 1
        delay = (disclosure.filing_date - txn.transaction_date).days
        overdue = delay - PTR_DEADLINE_DAYS
        if overdue <= 0:
            continue

        days_late.append(overdue)
        # Report the band, not a midpoint: the filing gives a range.
        if txn.amount_max is not None:
            late_value += txn.amount_max

        if worst is None or overdue > worst["days_late"]:
            worst = {
                "days_late": overdue,
                "ticker": txn.ticker,
                "transaction_date": txn.transaction_date.date().isoformat(),
                "filing_date": disclosure.filing_date.date().isoformat(),
                "document_id": disclosure.document_id,
            }

    if checkable == 0:
        return None

    late_count = len(days_late)

    return {
        "member_id": member.id,
        "member_name": f"{member.first_name} {member.last_name}",
        "bioguide_id": member.bioguide_id,
        "party": member.party.value if member.party else None,
        "state": member.state,
        "chamber": member.chamber.value if member.chamber else None,
        "transactions_checked": checkable,
        "filed_late": late_count,
        "on_time": checkable - late_count,
        "late_rate_percent": round(late_count / checkable * 100, 2),
        "mean_days_late": round(sum(days_late) / late_count, 1) if days_late else 0.0,
        "median_days_late": _median(days_late) if days_late else 0.0,
        "max_days_late": max(days_late) if days_late else 0,
        # Upper bound of the disclosed bands, not an estimate of actual value.
        "late_value_upper_bound": float(late_value),
        "worst_filing": worst,
        "deadline_days": PTR_DEADLINE_DAYS,
    }


def compliance_leaderboard(
    db: Session,
    min_transactions: int = 5,
    limit: int | None = None,
) -> Dict[str, Any]:
    """Rank members by late-filing rate.

    `min_transactions` guards against a member with one late filing out of one
    transaction topping a "100% late" ranking.
    """
    scores: List[Dict[str, Any]] = []

    for member in db.query(Member).all():
        score = member_compliance(db, member)
        if score and score["transactions_checked"] >= min_transactions:
            scores.append(score)

    scores.sort(key=lambda s: (-s["late_rate_percent"], -s["mean_days_late"]))

    total_checked = sum(s["transactions_checked"] for s in scores)
    total_late = sum(s["filed_late"] for s in scores)

    return {
        "members_ranked": len(scores),
        "min_transactions": min_transactions,
        "total_transactions_checked": total_checked,
        "total_filed_late": total_late,
        "overall_late_rate_percent": (
            round(total_late / total_checked * 100, 2) if total_checked else 0.0
        ),
        "deadline_days": PTR_DEADLINE_DAYS,
        "members": scores[:limit] if limit else scores,
        "note": (
            "Late means the Periodic Transaction Report was filed more than "
            f"{PTR_DEADLINE_DAYS} days after the transaction date, per the STOCK Act. "
            "Members with fewer than the minimum number of checkable transactions "
            "are excluded. Only Periodic Transaction Reports are counted; annual "
            "disclosures have different deadlines."
        ),
    }
