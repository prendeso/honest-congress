"""Maintenance for the materialized count columns on :class:`Member`.

``Member.disclosure_count`` and ``Member.anomaly_count`` are denormalized so
the members API can sort and filter on them without a correlated subquery per
row. Nothing kept them in sync -- this module had no callers at all -- so those
columns read as zero while `/api/members` filtered, sorted and returned them.

`recalculate_member_counts` is the only maintenance entry point. It is a full
idempotent recompute rather than incremental bookkeeping: the previous
per-member increment/decrement helpers were unused, and two of them called
``func.greatest``, which does not exist on SQLite.
"""

from typing import Dict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db import Anomaly, Disclosure, Member


def recalculate_member_counts(db: Session) -> Dict[str, int]:
    """Recompute both materialized counts for every member.

    Uses one UPDATE per column with a correlated subquery, so members with no
    disclosures or no anomalies are reset to zero in the same statement rather
    than needing a separate pass. Safe to run repeatedly.

    Returns a summary of what was recomputed.
    """
    disclosure_count = (
        select(func.count(Disclosure.id)).where(Disclosure.member_id == Member.id).scalar_subquery()
    )
    # Only the findings the site shows: the members table displays this beside
    # each name and sorts by it, and counting the ones `GET /api/anomalies/`
    # withholds for failing FDR correction ranked people by exactly the noise
    # the correction exists to hold back. Same filter as that endpoint -- a
    # NULL q-value is "untested", not "failed", and is counted.
    alpha = get_settings().fdr_alpha
    anomaly_count = (
        select(func.count(Anomaly.id))
        .where(Anomaly.member_id == Member.id)
        .where(or_(Anomaly.q_value.is_(None), Anomaly.q_value <= alpha))
        .scalar_subquery()
    )

    db.query(Member).update({"disclosure_count": disclosure_count}, synchronize_session=False)
    db.query(Member).update({"anomaly_count": anomaly_count}, synchronize_session=False)
    db.commit()

    return {
        "members": db.query(func.count(Member.id)).scalar() or 0,
        "disclosures": db.query(func.count(Disclosure.id)).scalar() or 0,
        "anomalies": db.query(func.count(Anomaly.id)).scalar() or 0,
    }
