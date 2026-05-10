"""Tier-2 detectors driven by QuiverQuant donors / lobbying / contracts data.

Three independent detectors, each scanning the ``transactions`` table for
trades that fall inside a suspicious time window relative to a *trigger
event* (a donation, a lobbying filing, or a federal contract award).

* **Donor conflict**: member traded a stock issued by a company that
  donated to them within ``donor_window_days``.
* **Lobbying overlap**: member traded a stock around a lobbying-disclosure
  filing date for that company (window ``lobbying_window_days`` days
  before *or* after).
* **Contract front-run**: member purchased a stock within
  ``contract_window_days`` *before* the company was awarded a federal
  contract — the directional case is the strongest insider signal.

All three return list-of-dict anomalies in the same shape as the existing
detectors so :func:`src.analysis.persist_anomalies` handles persistence.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.db.models import (
    CampaignDonation,
    Disclosure,
    GovernmentContract,
    LobbyingDisclosure,
    Transaction,
    TransactionType,
)

logger = logging.getLogger(__name__)


# Default windows. Tuned to match the rest of the detector defaults — large
# enough to catch realistic patterns, small enough that random coincidence
# stays low. Override per-call when calibrating.
DEFAULT_DONOR_WINDOW_DAYS = 90
DEFAULT_LOBBYING_WINDOW_DAYS = 30
DEFAULT_CONTRACT_WINDOW_DAYS = 30


def detect_donor_conflicts(
    db: Session, window_days: int = DEFAULT_DONOR_WINDOW_DAYS
) -> List[Dict[str, Any]]:
    """Flag trades made within ``window_days`` of a campaign donation.

    For each ``CampaignDonation`` row that has both a ``ticker`` and a
    ``donation_date``, look for transactions by the same member in the
    same ticker that fall inside a window centered on the donation date.

    The window is symmetric — we don't know which direction is more
    incriminating without context. A trade *after* a donation suggests
    the member acted on the relationship; a trade *before* suggests the
    donation was anticipated. Both deserve a flag.
    """
    anomalies: List[Dict[str, Any]] = []

    donations = (
        db.query(CampaignDonation)
        .filter(CampaignDonation.ticker.isnot(None))
        .filter(CampaignDonation.donation_date.isnot(None))
        .all()
    )

    for donation in donations:
        delta = timedelta(days=window_days)
        start = donation.donation_date - delta
        end = donation.donation_date + delta

        trades = (
            db.query(Transaction)
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .filter(
                Disclosure.member_id == donation.member_id,
                Transaction.ticker == donation.ticker,
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            )
            .all()
        )

        for txn in trades:
            days_apart = abs((txn.transaction_date - donation.donation_date).days)
            direction = "after" if txn.transaction_date >= donation.donation_date else "before"
            amount_str = ""
            if donation.amount is not None:
                amount_str = f" (${float(donation.amount):,.0f})"

            anomalies.append(
                {
                    "member_id": donation.member_id,
                    "anomaly_type": "donor_conflict",
                    "severity": "HIGH" if days_apart <= 30 else "MEDIUM",
                    "title": (
                        f"Donor conflict: traded {donation.ticker} {days_apart}d "
                        f"{direction} donation"
                    ),
                    "transaction_id": txn.id,
                    "computed_value": Decimal(str(days_apart)),
                    "threshold_value": Decimal(str(window_days)),
                    "description": (
                        f"Member traded {donation.ticker} on "
                        f"{txn.transaction_date.strftime('%Y-%m-%d')} "
                        f"({days_apart} days {direction} a donation from "
                        f"{donation.donor_name}{amount_str} on "
                        f"{donation.donation_date.strftime('%Y-%m-%d')}). "
                        f"Trades in companies that donate to a member's campaign "
                        f"raise conflict-of-interest concerns regardless of direction."
                    ),
                }
            )

    return anomalies


def detect_lobbying_overlaps(
    db: Session, window_days: int = DEFAULT_LOBBYING_WINDOW_DAYS
) -> List[Dict[str, Any]]:
    """Flag trades that fall within ``window_days`` of a lobbying filing.

    Lobbying disclosures don't single out a specific member — they're filed
    by the issuer. The signal is: a member traded the stock of a company
    that's actively trying to shape policy, around the time the company
    publicly disclosed that lobbying. Any member in any chamber is in scope.
    """
    anomalies: List[Dict[str, Any]] = []

    filings = db.query(LobbyingDisclosure).filter(LobbyingDisclosure.filed_date.isnot(None)).all()

    for filing in filings:
        delta = timedelta(days=window_days)
        start = filing.filed_date - delta
        end = filing.filed_date + delta

        trades = (
            db.query(Transaction, Disclosure.member_id)
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .filter(
                Transaction.ticker == filing.ticker,
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            )
            .all()
        )

        for txn, member_id in trades:
            days_apart = abs((txn.transaction_date - filing.filed_date).days)
            anomalies.append(
                {
                    "member_id": member_id,
                    "anomaly_type": "lobbying_overlap",
                    "severity": "MEDIUM",
                    "title": (
                        f"Lobbying overlap: traded {filing.ticker} near {filing.registrant} filing"
                    ),
                    "transaction_id": txn.id,
                    "computed_value": Decimal(str(days_apart)),
                    "threshold_value": Decimal(str(window_days)),
                    "description": (
                        f"Member traded {filing.ticker} on "
                        f"{txn.transaction_date.strftime('%Y-%m-%d')} "
                        f"({days_apart}d from a lobbying disclosure filed by "
                        f"{filing.registrant} on "
                        f"{filing.filed_date.strftime('%Y-%m-%d')}). "
                        f"The issuer is actively trying to shape federal policy "
                        f"around the time of the trade."
                    ),
                }
            )

    return anomalies


def detect_contract_front_runs(
    db: Session, window_days: int = DEFAULT_CONTRACT_WINDOW_DAYS
) -> List[Dict[str, Any]]:
    """Flag PURCHASES made within ``window_days`` BEFORE a contract award.

    Asymmetric on purpose — buying before the award is the front-run case;
    selling before / buying after are weaker signals. Future iterations
    can add the inverse direction once we have a way to distinguish
    publicly-known anticipated awards from genuinely-leaked ones.
    """
    anomalies: List[Dict[str, Any]] = []

    contracts = (
        db.query(GovernmentContract).filter(GovernmentContract.awarded_date.isnot(None)).all()
    )

    for contract in contracts:
        start = contract.awarded_date - timedelta(days=window_days)
        end = contract.awarded_date

        trades = (
            db.query(Transaction, Disclosure.member_id)
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .filter(
                Transaction.ticker == contract.ticker,
                Transaction.transaction_type == TransactionType.PURCHASE,
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            )
            .all()
        )

        for txn, member_id in trades:
            days_before = (contract.awarded_date - txn.transaction_date).days
            amount_str = f" (${float(contract.amount):,.0f})" if contract.amount is not None else ""
            anomalies.append(
                {
                    "member_id": member_id,
                    "anomaly_type": "contract_front_run",
                    "severity": "HIGH",
                    "title": (
                        f"Contract front-run: bought {contract.ticker} {days_before}d before award"
                    ),
                    "transaction_id": txn.id,
                    "computed_value": Decimal(str(days_before)),
                    "threshold_value": Decimal(str(window_days)),
                    "description": (
                        f"Member purchased {contract.ticker} on "
                        f"{txn.transaction_date.strftime('%Y-%m-%d')}, "
                        f"{days_before} days before the company was awarded a "
                        f"federal contract by {contract.agency or 'a federal agency'} "
                        f"on {contract.awarded_date.strftime('%Y-%m-%d')}{amount_str}. "
                        f"Buying ahead of a public contract award is one of the "
                        f"clearest insider-information signals available."
                    ),
                }
            )

    return anomalies


def run_tier2_detection(db: Session, persist: bool = True) -> Dict[str, Any]:
    """Run all three Tier-2 detectors. Mirrors run_advanced/run_extended."""
    from src.analysis import persist_anomalies

    logger.info("\n" + "=" * 70)
    logger.info("TIER-2 ANOMALY DETECTION (donors / lobbying / contracts)")
    logger.info("=" * 70 + "\n")

    donor_anomalies = detect_donor_conflicts(db)
    logger.info("Donor conflicts: %d", len(donor_anomalies))

    lobbying_anomalies = detect_lobbying_overlaps(db)
    logger.info("Lobbying overlaps: %d", len(lobbying_anomalies))

    contract_anomalies = detect_contract_front_runs(db)
    logger.info("Contract front-runs: %d", len(contract_anomalies))

    persisted = 0
    if persist:
        persisted += persist_anomalies(db, donor_anomalies)
        persisted += persist_anomalies(db, lobbying_anomalies)
        persisted += persist_anomalies(db, contract_anomalies)
        logger.info("Persisted %d new Tier-2 anomalies", persisted)

    return {
        "donor_anomalies": donor_anomalies,
        "lobbying_anomalies": lobbying_anomalies,
        "contract_anomalies": contract_anomalies,
        "persisted": persisted,
        "total": len(donor_anomalies) + len(lobbying_anomalies) + len(contract_anomalies),
    }
