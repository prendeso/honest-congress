"""Trades joined to what the member did in office.

Every other detector in this package asks a question about trading alone. These
two ask whether the member acted on an issuer's industry in their official
capacity near the time they traded it -- which is the thing Capitol Trades,
Unusual Whales and Quiver do not publish, because it needs a second dataset and
a defensible join rather than a prettier table of the same filings.

Two detectors, deliberately separate:

* **sponsorship_conflict** -- the member *sponsored* a bill whose CRS policy
  area identifies a sector, and traded in that sector inside the window. The
  strongest of the available signals: sponsorship is a single, dated,
  individually attributable act.
* **bill_jurisdiction_conflict** -- a bill was referred to a committee the
  member sits on, and they traded the affected sector inside the window. The
  dated counterpart to `committee_jurisdiction_conflict`, which says the same
  thing about a standing overlap with no event or date attached. Both are kept,
  under separate types: collapsing them would repeat the `sector_concentration`
  mistake, where two unrelated detectors shared one type string and their
  incompatible severities were persisted together.

What neither claims
-------------------
Not that the legislative act caused the trade, nor the reverse. The disclosures
report a date and a band, not a motive, and nothing here establishes intent.
What they do give is a specific, checkable coincidence: a named bill, a date,
and the tickers, so a reader can verify it on congress.gov themselves.

**Cosponsorship is stored but does not produce findings.** Measured on the live
API, one representative sponsored 71 bills and cosponsored 1,562. Cosponsoring
is 22x more common and nearly costless, so a detector that treated the two alike
would flag essentially everyone. Making it useful needs its own calibration, and
until that exists an unfired detector is better than a meaningless one.

**Coverage is bounded and the bound is reported.** Measured on 9,896 real
bills: **35% fall in one of the seven policy areas that identify an industry**
rather than a theme, and `sectors.SECTOR_TICKERS` currently recognises only
about 70 large-cap tickers -- which is the binding constraint, not the policy
areas. `coverage_report` measures both against the live database so an empty
result reads as "little was mappable" rather than "nothing was there".
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Set

from sqlalchemy.orm import Session

from src.analysis.sectors import SECTOR_TICKERS, classify, policy_area_sectors
from src.db.models import (
    Bill,
    BillCommittee,
    BillSponsorship,
    CommitteeAssignment,
    Disclosure,
    Member,
    Transaction,
)

logger = logging.getLogger(__name__)

SPONSORSHIP_ANOMALY_TYPE = "sponsorship_conflict"
JURISDICTION_ANOMALY_TYPE = "bill_jurisdiction_conflict"

# Symmetric, for the same reason `detect_donor_conflicts` uses a symmetric
# window: a trade after the legislative act suggests acting on it, a trade
# before suggests anticipating it, and this data cannot tell the two apart. 60
# days is asserted rather than calibrated -- `annotate_percentile_ranks` is what
# turns a finding into a defensible statement about where it sits.
DEFAULT_WINDOW_DAYS = 60

# Proximity bands for severity. Transparent and asserted; the percentile rank
# carries the comparative claim.
CLOSE_DAYS = 14
NEAR_DAYS = 30


def _parent_committee(committee_id: str | None) -> str | None:
    """The full-committee code behind an id that may name a subcommittee.

    congress-legislators ids are the parent thomas_id optionally followed by the
    subcommittee's own number -- "HSBA", "HSBA16". Jurisdiction questions are
    about the parent, and a member seated on a subcommittee sits inside its
    parent's remit.
    """
    if not committee_id:
        return None
    code = committee_id.strip().upper()
    return code[:4] or None


def _severity(days_apart: int) -> str:
    if days_apart <= CLOSE_DAYS:
        return "HIGH"
    if days_apart <= NEAR_DAYS:
        return "MEDIUM"
    return "LOW"


def _member_transactions(db: Session) -> Dict[int, List[Transaction]]:
    """Every disclosed transaction, grouped by member. One query, not N."""
    rows = (
        db.query(Transaction, Disclosure.member_id)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .all()
    )
    by_member: Dict[int, List[Transaction]] = defaultdict(list)
    for transaction, member_id in rows:
        by_member[member_id].append(transaction)
    return by_member


def _matching_trades(
    transactions: List[Transaction],
    sectors: Set[str],
    event_date: Any,
    window_days: int,
) -> List[Transaction]:
    """Trades in `sectors` falling within `window_days` either side of the event."""
    delta = timedelta(days=window_days)
    start, end = event_date - delta, event_date + delta
    matched = []
    for txn in transactions:
        if txn.transaction_date is None or not (start <= txn.transaction_date <= end):
            continue
        if classify(txn.ticker, txn.description) & sectors:
            matched.append(txn)
    return matched


def _describe(matched: List[Transaction]) -> tuple[List[str], int]:
    tickers = sorted({t.ticker.upper() for t in matched if t.ticker})
    return tickers, len(matched)


def detect_sponsorship_conflicts(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS
) -> List[Dict[str, Any]]:
    """Flag members who traded a sector around sponsoring a bill affecting it."""
    anomalies: List[Dict[str, Any]] = []
    by_member = _member_transactions(db)
    members = {m.id: m for m in db.query(Member).all()}

    sponsorships = (
        db.query(BillSponsorship, Bill)
        .join(Bill, BillSponsorship.bill_id == Bill.id)
        .filter(BillSponsorship.is_sponsor.is_(True))
        .filter(Bill.policy_area.isnot(None))
        .filter(Bill.introduced_date.isnot(None))
        .all()
    )

    for sponsorship, bill in sponsorships:
        sectors = set(policy_area_sectors(bill.policy_area))
        if not sectors:
            continue

        member = members.get(sponsorship.member_id)
        transactions = by_member.get(sponsorship.member_id) or []
        if member is None or not transactions:
            continue

        matched = _matching_trades(transactions, sectors, bill.introduced_date, window_days)
        if not matched:
            continue

        tickers, count = _describe(matched)
        closest = min(abs((t.transaction_date - bill.introduced_date).days) for t in matched)
        sector_list = ", ".join(sorted(sectors))

        anomalies.append(
            {
                "member_id": member.id,
                "member_name": f"{member.first_name} {member.last_name}",
                "chamber": member.chamber,
                "anomaly_type": SPONSORSHIP_ANOMALY_TYPE,
                "severity": _severity(closest),
                "title": f"Traded {sector_list} around sponsoring {bill.citation}",
                "bill": f"{bill.congress} {bill.citation}",
                "bill_title": bill.title,
                "policy_area": bill.policy_area,
                "sectors": sorted(sectors),
                "tickers": tickers,
                "matched_trades": count,
                "days_from_introduction": closest,
                "computed_value": Decimal(str(closest)),
                "threshold_value": Decimal(str(window_days)),
                "description": (
                    f"The member sponsored {bill.citation} ({bill.congress}th Congress), "
                    f"introduced {bill.introduced_date.date()}, which CRS classifies under "
                    f'"{bill.policy_area}" -- covering the {sector_list} sector. '
                    f"They disclosed {count} trade(s) in that sector within {window_days} days "
                    f"of introduction, the closest {closest} day(s) away"
                    f"{': ' + ', '.join(tickers) if tickers else ''}. "
                    f"Sponsorship and the trade dates are both public record; this is a "
                    f"disclosed coincidence in time and does not establish that either "
                    f"caused the other, or that the bill affected these particular issuers."
                ),
            }
        )

    logger.info("Sponsorship conflicts: %d findings", len(anomalies))
    return anomalies


def detect_bill_jurisdiction_conflicts(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS
) -> List[Dict[str, Any]]:
    """Flag trades around a bill reaching a committee the member sits on."""
    anomalies: List[Dict[str, Any]] = []
    by_member = _member_transactions(db)
    members = {m.id: m for m in db.query(Member).all()}

    # member_id -> parent committee code -> the name to cite in the finding.
    seats: Dict[int, Dict[str, str]] = defaultdict(dict)
    for assignment in db.query(CommitteeAssignment).all():
        parent = _parent_committee(assignment.committee_id)
        if parent:
            seats[assignment.member_id].setdefault(parent, assignment.committee_name)

    referrals = (
        db.query(BillCommittee, Bill)
        .join(Bill, BillCommittee.bill_id == Bill.id)
        .filter(BillCommittee.activity_date.isnot(None))
        .filter(Bill.policy_area.isnot(None))
        .all()
    )

    for referral, bill in referrals:
        sectors = set(policy_area_sectors(bill.policy_area))
        if not sectors:
            continue
        parent = _parent_committee(referral.committee_id)
        if not parent:
            continue

        for member_id, member_seats in seats.items():
            if parent not in member_seats:
                continue
            member = members.get(member_id)
            transactions = by_member.get(member_id) or []
            if member is None or not transactions:
                continue

            matched = _matching_trades(transactions, sectors, referral.activity_date, window_days)
            if not matched:
                continue

            tickers, count = _describe(matched)
            closest = min(abs((t.transaction_date - referral.activity_date).days) for t in matched)
            committee_name = referral.committee_name or member_seats[parent]
            sector_list = ", ".join(sorted(sectors))
            activity = (referral.activity or "referred").lower()

            anomalies.append(
                {
                    "member_id": member.id,
                    "member_name": f"{member.first_name} {member.last_name}",
                    "chamber": member.chamber,
                    "anomaly_type": JURISDICTION_ANOMALY_TYPE,
                    "severity": _severity(closest),
                    "title": f"Traded {sector_list} around {bill.citation} reaching {committee_name}",
                    "bill": f"{bill.congress} {bill.citation}",
                    "bill_title": bill.title,
                    "policy_area": bill.policy_area,
                    "committee": committee_name,
                    "committee_id": referral.committee_id,
                    "activity": referral.activity,
                    "sectors": sorted(sectors),
                    "tickers": tickers,
                    "matched_trades": count,
                    "days_from_activity": closest,
                    "computed_value": Decimal(str(closest)),
                    "threshold_value": Decimal(str(window_days)),
                    "description": (
                        f"{bill.citation} ({bill.congress}th Congress) was {activity} "
                        f"{committee_name} on {referral.activity_date.date()}. The member sits "
                        f"on that committee, and disclosed {count} trade(s) in the "
                        f"{sector_list} sector within {window_days} days of that date, the "
                        f"closest {closest} day(s) away"
                        f"{': ' + ', '.join(tickers) if tickers else ''}. "
                        f"Committee membership, the referral date and the trade dates are all "
                        f"public record. This is a disclosed overlap in time; it does not "
                        f"establish that the seat or the bill influenced the trades, and no "
                        f"price analysis is performed."
                    ),
                }
            )

    logger.info("Bill jurisdiction conflicts: %d findings", len(anomalies))
    return anomalies


def bills_worth_committee_lookup(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS, limit: int | None = None
) -> List[Bill]:
    """Bills whose committee referrals are worth one request each.

    Referrals cost a request per bill and there are tens of thousands of bills,
    so this narrows to the ones that could actually produce a finding: a
    sector-mapped policy area, a member who touched the bill, and a trade by
    that member in that sector near the bill's own dates. The same inversion the
    FEC and LDA ingesters use.
    """
    by_member = _member_transactions(db)

    rows = (
        db.query(Bill, BillSponsorship.member_id)
        .join(BillSponsorship, BillSponsorship.bill_id == Bill.id)
        .filter(~Bill.committees_fetched)
        .filter(Bill.policy_area.isnot(None))
        .all()
    )

    candidates: Dict[int, Bill] = {}
    for bill, member_id in rows:
        if bill.id in candidates:
            continue
        sectors = set(policy_area_sectors(bill.policy_area))
        if not sectors:
            continue
        anchor = bill.introduced_date or bill.latest_action_date
        if anchor is None:
            continue
        transactions = by_member.get(member_id) or []
        # Widened, because the referral date is not yet known and sits somewhere
        # after introduction.
        if _matching_trades(transactions, sectors, anchor, window_days * 2):
            candidates[bill.id] = bill

    selected = list(candidates.values())
    logger.info(
        "Committee referrals worth fetching: %d of %d unfetched bills", len(selected), len(rows)
    )
    return selected[:limit] if limit else selected


def coverage_report(db: Session) -> Dict[str, Any]:
    """What fraction of the data these detectors can actually see.

    Reported rather than assumed, because all three of these ceilings are real
    and an unreported ceiling turns a coverage problem into an apparent clean
    result.
    """
    total_bills = db.query(Bill).count()
    unclassified = db.query(Bill).filter(Bill.policy_area.is_(None)).count()
    mapped = sum(
        1
        for (area,) in db.query(Bill.policy_area).filter(Bill.policy_area.isnot(None)).all()
        if policy_area_sectors(area)
    )

    traded = {
        (t[0] or "").strip().upper()
        for t in db.query(Transaction.ticker).filter(Transaction.ticker.isnot(None)).distinct()
        if (t[0] or "").strip()
    }
    known = {t for t in traded if any(t in symbols for symbols in SECTOR_TICKERS.values())}

    return {
        "bills": total_bills,
        "bills_without_policy_area": unclassified,
        "bills_mapped_to_a_sector": mapped,
        "distinct_traded_tickers": len(traded),
        "traded_tickers_with_a_known_sector": len(known),
        "sponsorships": db.query(BillSponsorship)
        .filter(BillSponsorship.is_sponsor.is_(True))
        .count(),
        "cosponsorships": db.query(BillSponsorship)
        .filter(BillSponsorship.is_sponsor.is_(False))
        .count(),
        "committee_referrals": db.query(BillCommittee).count(),
    }


def run_legislation_detection(db: Session, persist: bool = True) -> Dict[str, Any]:
    """Run both legislative detectors and optionally persist their findings."""
    from src.analysis import persist_anomalies

    sponsorship = detect_sponsorship_conflicts(db)
    jurisdiction = detect_bill_jurisdiction_conflicts(db)

    persisted = 0
    if persist:
        persisted += persist_anomalies(db, sponsorship)
        persisted += persist_anomalies(db, jurisdiction)

    return {
        "sponsorship_conflicts": sponsorship,
        "bill_jurisdiction_conflicts": jurisdiction,
        "persisted": persisted,
        "total": len(sponsorship) + len(jurisdiction),
        "coverage": coverage_report(db),
    }
