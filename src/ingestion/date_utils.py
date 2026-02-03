from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Ensure ingestion never stores future dates; normalize tz-aware input.


def _normalize_datetime(value: datetime) -> datetime:
    """Convert tz-aware datetimes to naive UTC for consistent comparisons."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def coerce_non_future(value: Optional[datetime], now: Optional[datetime] = None) -> Optional[datetime]:
    """Return value if not in the future; otherwise None."""
    if value is None:
        return None

    now = now or datetime.utcnow()
    normalized = _normalize_datetime(value)
    return normalized if normalized <= now else None


def choose_filing_date(
    filing_date: Optional[datetime],
    filing_year: Optional[int],
    now: Optional[datetime] = None,
) -> datetime:
    """Choose a safe filing date, preferring real data but never future dates."""
    now = now or datetime.utcnow()
    candidate = filing_date

    if candidate is None and filing_year:
        candidate = datetime(filing_year, 12, 31)

    if candidate is None:
        return now

    normalized = _normalize_datetime(candidate)
    return normalized if normalized <= now else now


def choose_transaction_date(
    transaction_date: Optional[datetime],
    fallback_date: Optional[datetime],
    now: Optional[datetime] = None,
) -> datetime:
    """Choose a safe transaction date from parsed or fallback dates."""
    now = now or datetime.utcnow()

    candidate = coerce_non_future(transaction_date, now)
    if candidate is not None:
        return candidate

    candidate = coerce_non_future(fallback_date, now)
    return candidate if candidate is not None else now
