"""STOCK Act filing compliance, per member.

The STOCK Act requires a Periodic Transaction Report within 30 days of a member
becoming aware of a covered transaction, and no later than 45 days after the
transaction itself. **45 days is the deadline this measures, and the reason is
the statute rather than the record.** House PTRs DO print a Notification Date
-- this module's own docstring used to say they did not -- but 5 U.S.C.
13104(l) makes the 45-day prong an absolute cap: a report is due within 30 days
of notification "but in no case later than 45 days after such transaction". A
notification date can only shorten a filer's window, never extend it, so it
never excuses a filing past 45 days. `trade_analyzer` publishes it as context
on the finding; nothing here scores against it.

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
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from sqlalchemy.orm import Session

from src.analysis.restatements import drop_restated_pairs
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
    """Filing punctuality for one member, or None if nothing is checkable."""
    rows = (
        db.query(Transaction, Disclosure)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(
            Disclosure.member_id == member.id,
            Disclosure.is_ptr.is_(True),
        )
        .all()
    )
    # Restated rows dropped, earliest filing kept: a trade refiled by a later
    # amendment must not be re-scored against the amendment's date, or a member
    # who corrected a filing is reported as having filed late.
    rows = drop_restated_pairs(rows)
    return _score(member, rows)


def _score(member: Member, rows: Sequence[Any]) -> Dict[str, Any] | None:
    """Score one member from their (transaction, disclosure) rows.

    Split out so the leaderboard can hand it pre-fetched rows instead of
    issuing a query per member. There is one implementation of the scoring and
    both callers use it, so the single-member endpoint and the leaderboard
    cannot drift apart.

    A transaction is only checkable when both its own date and the filing date
    of its disclosure are known. Both columns are currently NOT NULL, so the
    guard below is defensive rather than reachable -- but if either is ever
    relaxed, an unknown date must drop out of the denominator rather than be
    silently counted as filed on time, which would understate the late rate.
    """
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


def late_filing_rate(db: Session) -> Dict[str, Any]:
    """Just the headline: how many checkable trades were filed late.

    The landing page wants one percentage. It used to get it by building the
    entire leaderboard, which scores every filer individually -- far too much
    work for a number on a hero card, fetched on every page load.

    Two date columns in one query, compared in Python. Deliberately not a SQL
    date-difference: that needs `julianday` on SQLite and interval arithmetic
    on Postgres, and a dialect branch inside a query expression is a good way
    to ship something that works in the tests and fails in production. The
    comparison here is the same one `_score` makes, so the headline and the
    leaderboard cannot disagree.
    """
    rows = drop_restated_pairs(
        db.query(Transaction, Disclosure)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Disclosure.is_ptr.is_(True))
        .all()
    )

    checked = 0
    late = 0
    for transaction, disclosure in rows:
        transaction_date, filing_date = transaction.transaction_date, disclosure.filing_date
        if not transaction_date or not filing_date:
            continue
        checked += 1
        if (filing_date - transaction_date).days - PTR_DEADLINE_DAYS > 0:
            late += 1

    return {
        "transactions_checked": checked,
        "filed_late": late,
        "late_rate_percent": round(late / checked * 100, 2) if checked else 0.0,
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
    # One query, not one per member. This iterated `db.query(Member).all()` and
    # called `member_compliance` inside the loop -- on the production roster of
    # 12,766 members that is 12,766 round trips, and it took 48 seconds to
    # return an empty leaderboard. `/api/insights` calls this on every load of
    # the landing page, so the front page of the site hung.
    #
    # Driving from the join also scopes the work correctly: only members who
    # actually filed a PTR transaction can score, and the join yields exactly
    # those. Members with nothing to check were being fetched and discarded.
    rows = drop_restated_pairs(
        db.query(Transaction, Disclosure)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Disclosure.is_ptr.is_(True))
        .all()
    )

    by_member: Dict[int, List[Any]] = defaultdict(list)
    for txn, disclosure in rows:
        by_member[disclosure.member_id].append((txn, disclosure))

    if not by_member:
        members: Dict[int, Member] = {}
    else:
        members = {m.id: m for m in db.query(Member).filter(Member.id.in_(by_member)).all()}

    scores: List[Dict[str, Any]] = []
    for member_id, member_rows in by_member.items():
        member = members.get(member_id)
        if member is None:
            continue
        score = _score(member, member_rows)
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
