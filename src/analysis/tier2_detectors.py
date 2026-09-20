"""Tier-2 detectors driven by donor / lobbying / contract trigger events.

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

Their feeds are :mod:`src.ingestion.fec`, :mod:`src.ingestion.lda` and
:mod:`src.ingestion.usaspending` -- all official, public-domain sources. All
three publish company *names*, so the tickers these detectors join on come from
:mod:`src.ingestion.sec_tickers`, and coverage there bounds what any of this can
find. `detectors_without_source_data` reports a table nothing has filled.

All three return list-of-dict anomalies in the same shape as the existing
detectors so :func:`src.analysis.persist_anomalies` handles persistence.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Collection, Dict, List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.analysis.restatements import drop_restated_records
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


def award_action_criteria() -> List[Any]:
    """Which stored contract rows are an *award* a purchase could run ahead of.

    USASpending's transaction feed is award *actions*, not awards, and an action
    can take money off a contract as easily as put it on. Asking it for Lockheed
    Martin since 2023 returns a single action at **-$1,882,437,667** -- a
    deobligation, the Navy releasing money it had committed and no longer owed.
    Nothing about that is an award being made, and a member who bought the stock
    in the thirty days before it is not, on the face of it, ahead of good news.
    Yet `detect_contract_front_runs` would have reported exactly that, in the
    detector's own words, as "awarded a federal contract ... ($-1,882,437,668)".

    A zero-dollar action is excluded for the same reason: it is an
    administrative modification -- a restructure, a re-code, an address change --
    that obligates nothing.

    A row with **no** amount at all is kept. An absent figure is not evidence of
    a deobligation, and dropping it would quietly narrow coverage on the
    strength of a missing field.

    This is one function rather than a filter written twice because
    :func:`src.analysis.significance._collect_contracts` builds the null model
    from the same table. If the detector and its null model disagree about which
    rows are awards, the q-value is measured against a set of events the finding
    was never drawn from -- and that is the kind of disagreement that stays
    silent forever.
    """
    return [
        GovernmentContract.awarded_date.isnot(None),
        or_(GovernmentContract.amount.is_(None), GovernmentContract.amount > 0),
    ]


def _trades_by_ticker(
    db: Session,
    tickers: Collection[str],
    *,
    purchases_only: bool = False,
) -> Dict[str, List[Any]]:
    """Every disclosed trade in these tickers, grouped by ticker, in ONE query.

    All three detectors here walk a table of trigger events -- donations,
    lobbying filings, contract awards -- and ask, for each one, "what was traded
    in this ticker near this date". Each asked the database, once per trigger
    row. That is an N+1, and the N is not small or static: lobbying alone holds
    4,920 rows, and the contract feed went from roughly 200 to roughly 10,000
    when it stopped taking a global top-300 slice. Every one of those iterations
    is a network round trip, because `analyze` runs on a GitHub runner against a
    hosted database.

    The date arithmetic never needed the database. Only the ticker does, so the
    ticker lookup moves out of the loop and the windows are matched in Python.

    Columns rather than entities: the loops read `id`, `transaction_date` and
    the member behind the filing, nothing else. Selecting those four keeps the
    result a list of lightweight rows instead of hydrating tens of thousands of
    ORM objects that are then read once.

    Ordered by transaction id so the output is deterministic. It was not before
    -- neither this query nor the trigger-table scan carried an ORDER BY -- and
    the order is load-bearing in one specific way: several trigger rows can hit
    the same trade, `persist_anomalies` keeps one finding per
    (member, type, transaction), and which description that finding carries was
    therefore whatever the database happened to return first.
    """
    wanted = {t for t in tickers if t}
    grouped: Dict[str, List[Any]] = {}
    if not wanted:
        return grouped

    query = (
        db.query(
            Transaction.id.label("id"),
            Transaction.ticker.label("ticker"),
            Transaction.transaction_date.label("transaction_date"),
            Disclosure.member_id.label("member_id"),
            # Carried so restated rows can be recognised. An amendment refiles
            # its original in full, and these findings are keyed on
            # `transaction_id`, so `identity_of` cannot collapse the duplicate
            # downstream -- each real trade would publish two findings.
            Transaction.disclosure_id.label("disclosure_id"),
            Transaction.transaction_type.label("transaction_type"),
            Transaction.description.label("description"),
            Transaction.amount_min.label("amount_min"),
            Transaction.amount_max.label("amount_max"),
            Transaction.owner.label("owner"),
            # Carried because `drop_restatements` drops rows the filer
            # struck out, and a projection that omits the column keeps
            # them silently.
            Transaction.filing_status.label("filing_status"),
            Disclosure.filing_date.label("filing_date"),
        )
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(
            Transaction.ticker.in_(wanted),
            # The per-row queries compared transaction_date with >= and <=,
            # which drops NULLs in SQL. Doing the comparison in Python would
            # raise on them instead, so they are excluded here as well.
            Transaction.transaction_date.isnot(None),
        )
    )
    if purchases_only:
        query = query.filter(Transaction.transaction_type == TransactionType.PURCHASE)

    for row in drop_restated_records(query.order_by(Transaction.id).all()):
        grouped.setdefault(row.ticker, []).append(row)
    return grouped


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
        .order_by(CampaignDonation.id)
        .all()
    )

    # One query for every ticker any donation names, rather than one per
    # donation. This detector also filters on the donating member, which stays
    # a comparison in the loop -- a ticker's trades are a short list.
    by_ticker = _trades_by_ticker(db, {d.ticker for d in donations})
    delta = timedelta(days=window_days)

    for donation in donations:
        start = donation.donation_date - delta
        end = donation.donation_date + delta

        trades = [
            txn
            for txn in by_ticker.get(donation.ticker, ())
            if txn.member_id == donation.member_id and start <= txn.transaction_date <= end
        ]

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

    filings = (
        db.query(LobbyingDisclosure)
        .filter(LobbyingDisclosure.filed_date.isnot(None))
        .order_by(LobbyingDisclosure.id)
        .all()
    )

    # 4,920 filings in the last run, so 4,920 round trips. Now one.
    by_ticker = _trades_by_ticker(db, {f.ticker for f in filings})
    delta = timedelta(days=window_days)

    for filing in filings:
        start = filing.filed_date - delta
        end = filing.filed_date + delta

        trades = [
            txn for txn in by_ticker.get(filing.ticker, ()) if start <= txn.transaction_date <= end
        ]

        for txn in trades:
            member_id = txn.member_id
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
        db.query(GovernmentContract)
        .filter(*award_action_criteria())
        .order_by(GovernmentContract.id)
        .all()
    )

    # Purchases only, pushed into the one query rather than repeated per award.
    # This is the loop the ticker-driven contract feed lengthened most.
    by_ticker = _trades_by_ticker(db, {c.ticker for c in contracts}, purchases_only=True)
    delta = timedelta(days=window_days)

    for contract in contracts:
        start = contract.awarded_date - delta
        end = contract.awarded_date

        trades = [
            txn
            for txn in by_ticker.get(contract.ticker, ())
            if start <= txn.transaction_date <= end
        ]

        for txn in trades:
            member_id = txn.member_id
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
