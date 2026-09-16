"""Trades in sectors the member's own committees oversee.

The previous detector of this name had no committee data -- its assignment
table was an empty dict -- so it substring-matched tickers against sector
keyword lists, where ``"ba"`` matched "Alibaba". It also emitted its findings as
``sector_concentration``, colliding with an unrelated TradeAnalyzer detector of
the same name.

This version joins real assignments (``committee_assignments``, from
congress-legislators) to trades classified by :mod:`src.analysis.sectors`.

What it does and does not claim: a member sitting on a committee whose remit
covers a sector, who also traded in that sector, is a disclosed
conflict-of-interest *pattern*. It is not evidence that the committee seat
influenced the trade, and this detector cannot establish that. The emitted
description says so.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.analysis.restatements import member_transactions
from src.analysis.sectors import SectorIndex, committee_sectors
from src.db.models import CommitteeAssignment, Member, Transaction

logger = logging.getLogger(__name__)

ANOMALY_TYPE = "committee_jurisdiction_conflict"

# A member on a broad committee will always touch its sectors somewhere. Require
# a meaningful share of their trading, not a single incidental holding.
MIN_TRADES_IN_SECTOR = 3
MIN_SHARE_OF_TRADES = 0.20


def detect_committee_jurisdiction_conflicts(db: Session) -> List[Dict[str, Any]]:
    """Flag members trading in sectors their committees oversee."""
    anomalies: List[Dict[str, Any]] = []

    members = db.query(Member).all()
    index = SectorIndex.from_db(db)

    for member in members:
        assignments = (
            db.query(CommitteeAssignment).filter(CommitteeAssignment.member_id == member.id).all()
        )
        if not assignments:
            continue

        # Map each overseen sector back to the committees responsible, so the
        # finding can name them.
        sector_to_committees: Dict[str, List[str]] = defaultdict(list)
        for assignment in assignments:
            for sector in committee_sectors(assignment.committee_id):
                # Prefer the parent committee's name for a subcommittee seat.
                sector_to_committees[sector].append(assignment.committee_name)

        if not sector_to_committees:
            continue

        transactions = member_transactions(db, member.id)
        if not transactions:
            continue

        by_sector: Dict[str, List[Transaction]] = defaultdict(list)
        for txn in transactions:
            for sector in index.classify(txn.ticker, txn.description):
                by_sector[sector].append(txn)

        total_trades = len(transactions)

        for sector, committees in sector_to_committees.items():
            matched = by_sector.get(sector, [])
            share = len(matched) / total_trades if total_trades else 0.0

            if len(matched) < MIN_TRADES_IN_SECTOR or share < MIN_SHARE_OF_TRADES:
                continue

            committee_names = sorted(set(committees))
            tickers = sorted({t.ticker for t in matched if t.ticker})

            anomalies.append(
                {
                    "member_id": member.id,
                    "member_name": f"{member.first_name} {member.last_name}",
                    "chamber": member.chamber,
                    "anomaly_type": ANOMALY_TYPE,
                    "severity": "HIGH" if share >= 0.5 else "MEDIUM",
                    "title": f"Traded {sector} while serving on overseeing committee",
                    "sector": sector,
                    "committees": committee_names,
                    "matched_trades": len(matched),
                    "total_trades": total_trades,
                    "share_of_trades": round(share * 100, 2),
                    "tickers": tickers,
                    "computed_value": Decimal(str(round(share * 100, 2))),
                    "threshold_value": Decimal(str(round(MIN_SHARE_OF_TRADES * 100, 2))),
                    "description": (
                        f"{len(matched)} of {total_trades} disclosed trades "
                        f"({share * 100:.0f}%) are in the {sector} sector, while the member "
                        f"serves on {', '.join(committee_names)}. "
                        f"Tickers: {', '.join(tickers) if tickers else 'n/a'}. "
                        f"This is a disclosed overlap between committee remit and trading "
                        f"activity; it does not establish that the seat influenced the "
                        f"trades, and no timing or price analysis is performed."
                    ),
                }
            )

    logger.info("Committee jurisdiction conflicts: %d findings", len(anomalies))
    return anomalies


def run_committee_conflict_detection(db: Session, persist: bool = True) -> Dict[str, Any]:
    """Run the detector and optionally persist its findings."""
    from src.analysis import persist_anomalies

    anomalies = detect_committee_jurisdiction_conflicts(db)

    persisted = 0
    if persist:
        persisted = persist_anomalies(db, anomalies)

    return {
        "anomalies": anomalies,
        "total": len(anomalies),
        "persisted": persisted,
    }
