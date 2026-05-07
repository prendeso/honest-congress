"""
Utility functions to maintain materialized count columns on the Member model.

These functions should be called whenever disclosures or anomalies are added/deleted.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db import Anomaly, Disclosure, Member


def update_member_disclosure_count(db: Session, member_id: int) -> None:
    """
    Recalculate and update the disclosure_count for a specific member.

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    count = db.query(func.count(Disclosure.id)).filter(Disclosure.member_id == member_id).scalar()

    db.query(Member).filter(Member.id == member_id).update({"disclosure_count": count or 0})
    db.commit()


def update_member_anomaly_count(db: Session, member_id: int) -> None:
    """
    Recalculate and update the anomaly_count for a specific member.

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    count = db.query(func.count(Anomaly.id)).filter(Anomaly.member_id == member_id).scalar()

    db.query(Member).filter(Member.id == member_id).update({"anomaly_count": count or 0})
    db.commit()


def update_all_member_counts(db: Session) -> dict:
    """
    Recalculate and update counts for all members.

    This is useful for bulk updates or data corrections.

    Args:
        db: Database session

    Returns:
        Dictionary with update statistics
    """
    # Update disclosure counts
    disclosure_counts = (
        db.query(Disclosure.member_id, func.count(Disclosure.id).label("count"))
        .group_by(Disclosure.member_id)
        .all()
    )

    disclosure_updates = 0
    for member_id, count in disclosure_counts:
        db.query(Member).filter(Member.id == member_id).update({"disclosure_count": count})
        disclosure_updates += 1

    # Reset to 0 for members with no disclosures
    db.query(Member).filter(~Member.id.in_([m_id for m_id, _ in disclosure_counts])).update(
        {"disclosure_count": 0}, synchronize_session=False
    )

    # Update anomaly counts
    anomaly_counts = (
        db.query(Anomaly.member_id, func.count(Anomaly.id).label("count"))
        .group_by(Anomaly.member_id)
        .all()
    )

    anomaly_updates = 0
    for member_id, count in anomaly_counts:
        db.query(Member).filter(Member.id == member_id).update({"anomaly_count": count})
        anomaly_updates += 1

    # Reset to 0 for members with no anomalies
    db.query(Member).filter(~Member.id.in_([m_id for m_id, _ in anomaly_counts])).update(
        {"anomaly_count": 0}, synchronize_session=False
    )

    db.commit()

    return {
        "disclosure_updates": disclosure_updates,
        "anomaly_updates": anomaly_updates,
        "total_members": db.query(Member).count(),
    }


def increment_member_disclosure_count(db: Session, member_id: int) -> None:
    """
    Increment the disclosure_count for a member (faster than recounting).

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    db.query(Member).filter(Member.id == member_id).update(
        {"disclosure_count": Member.disclosure_count + 1}
    )
    db.commit()


def decrement_member_disclosure_count(db: Session, member_id: int) -> None:
    """
    Decrement the disclosure_count for a member (faster than recounting).

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    db.query(Member).filter(Member.id == member_id).update(
        {"disclosure_count": func.greatest(Member.disclosure_count - 1, 0)}
    )
    db.commit()


def increment_member_anomaly_count(db: Session, member_id: int) -> None:
    """
    Increment the anomaly_count for a member (faster than recounting).

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    db.query(Member).filter(Member.id == member_id).update(
        {"anomaly_count": Member.anomaly_count + 1}
    )
    db.commit()


def decrement_member_anomaly_count(db: Session, member_id: int) -> None:
    """
    Decrement the anomaly_count for a member (faster than recounting).

    Args:
        db: Database session
        member_id: ID of the member to update
    """
    db.query(Member).filter(Member.id == member_id).update(
        {"anomaly_count": func.greatest(Member.anomaly_count - 1, 0)}
    )
    db.commit()
