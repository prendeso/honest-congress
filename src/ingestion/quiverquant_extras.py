"""Ingestion for QuiverQuant Tier-2 datasets: corporate donors, lobbying,
government contracts.

Each function fetches via :class:`QuiverQuantClient` and upserts rows into
the corresponding model defined in ``src.db.models``. The detectors in
``src.analysis.tier2_detectors`` consume these tables and emit anomalies.

Why a separate module: keeping the Tier-1 trade-ingestion path
(``src.ingestion.quiverquant``) focused on the highest-traffic ingestion
flow makes that file easier to reason about. Tier-2 ingestion runs less
frequently (typically only on Full Refresh or on demand) and lives here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable

from sqlalchemy.orm import Session

from src.db.models import CampaignDonation, GovernmentContract, LobbyingDisclosure, Member
from src.ingestion.quiverquant import QuiverQuantClient

logger = logging.getLogger(__name__)


# Date formats QuiverQuant typically uses across these endpoints.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")


def _parse_date(raw: Any) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def ingest_corporate_donors(
    db: Session,
    bioguide_ids: Iterable[str] | None = None,
    cycle: str | None = None,
) -> Dict[str, int]:
    """Pull donations from QuiverQuant for the supplied members.

    QuiverQuant requires at least one filter on ``/bulk/corporatedonors`` to
    avoid returning the entire dataset. We default to iterating over every
    member with a non-null ``bioguide_id`` already in the DB — that keeps
    the volume bounded and avoids ingesting donations for politicians we
    don't track anyway.
    """
    try:
        client = QuiverQuantClient()
    except ValueError as e:
        logger.error("QuiverQuant configuration error: %s", e)
        return {"imported": 0, "duplicates": 0, "errors": 0}

    results = {"imported": 0, "duplicates": 0, "errors": 0}

    if bioguide_ids is None:
        bioguide_ids = [
            row[0]
            for row in db.query(Member.bioguide_id).filter(Member.bioguide_id.isnot(None)).all()
        ]

    for bioguide_id in bioguide_ids:
        member = db.query(Member).filter(Member.bioguide_id == bioguide_id).first()
        if not member:
            results["errors"] += 1
            continue

        donations = client.get_corporate_donors(bioguide_id=bioguide_id, cycle=cycle)
        for d in donations:
            try:
                ticker = (d.get("Ticker") or "").upper().strip() or None
                donor_name = (d.get("Donor") or d.get("DonorName") or "").strip()
                if not donor_name:
                    results["errors"] += 1
                    continue

                amount = _parse_decimal(d.get("Amount") or d.get("AmountTotal"))
                cycle_val = (d.get("Cycle") or "").strip() or None
                txn_type = (d.get("TransactionType") or "").strip() or None
                donation_date = _parse_date(
                    d.get("Date") or d.get("TransactionDate") or d.get("FiledDate")
                )

                # Dedup on (member, donor, ticker, amount, date) — the API
                # doesn't ship a stable id, and exact duplicates can appear
                # across cycles.
                existing = (
                    db.query(CampaignDonation)
                    .filter(
                        CampaignDonation.member_id == member.id,
                        CampaignDonation.donor_name == donor_name,
                        CampaignDonation.ticker == ticker,
                        CampaignDonation.amount == amount,
                        CampaignDonation.donation_date == donation_date,
                    )
                    .first()
                )
                if existing:
                    results["duplicates"] += 1
                    continue

                db.add(
                    CampaignDonation(
                        member_id=member.id,
                        ticker=ticker,
                        donor_name=donor_name[:255],
                        amount=amount,
                        cycle=cycle_val[:10] if cycle_val else None,
                        transaction_type=txn_type[:50] if txn_type else None,
                        donation_date=donation_date,
                    )
                )
                results["imported"] += 1
            except Exception:
                logger.exception("Failed to ingest donation row")
                results["errors"] += 1

    db.commit()
    logger.info("Donor ingestion complete: %s", results)
    return results


def ingest_lobbying(
    db: Session,
    tickers: Iterable[str] | None = None,
) -> Dict[str, int]:
    """Pull lobbying disclosures for the given tickers.

    If ``tickers`` is None, defaults to the distinct set of tickers we
    already see in our ``transactions`` table — there's no point ingesting
    lobbying data for stocks no member has ever traded.
    """
    try:
        client = QuiverQuantClient()
    except ValueError as e:
        logger.error("QuiverQuant configuration error: %s", e)
        return {"imported": 0, "duplicates": 0, "errors": 0}

    results = {"imported": 0, "duplicates": 0, "errors": 0}

    if tickers is None:
        from src.db.models import Transaction

        tickers = [
            row[0]
            for row in db.query(Transaction.ticker)
            .filter(Transaction.ticker.isnot(None))
            .distinct()
            .all()
        ]

    for ticker in tickers:
        rows = client.get_lobbying(ticker=ticker)
        for r in rows:
            try:
                registrant = (r.get("Registrant") or r.get("Lobbyist") or "").strip()
                if not registrant:
                    results["errors"] += 1
                    continue

                client_name = (r.get("Client") or "").strip() or None
                amount = _parse_decimal(r.get("Amount"))
                filed_date = _parse_date(
                    r.get("Date") or r.get("FilingDate") or r.get("filing_date")
                )
                issue_codes = (r.get("Issue") or r.get("IssueAreaCode") or "").strip() or None

                existing = (
                    db.query(LobbyingDisclosure)
                    .filter(
                        LobbyingDisclosure.ticker == ticker,
                        LobbyingDisclosure.registrant == registrant,
                        LobbyingDisclosure.filed_date == filed_date,
                        LobbyingDisclosure.amount == amount,
                    )
                    .first()
                )
                if existing:
                    results["duplicates"] += 1
                    continue

                db.add(
                    LobbyingDisclosure(
                        ticker=ticker,
                        registrant=registrant[:255],
                        client=client_name[:255] if client_name else None,
                        amount=amount,
                        filed_date=filed_date,
                        issue_codes=issue_codes,
                    )
                )
                results["imported"] += 1
            except Exception:
                logger.exception("Failed to ingest lobbying row")
                results["errors"] += 1

    db.commit()
    logger.info("Lobbying ingestion complete: %s", results)
    return results


def ingest_government_contracts(
    db: Session,
    tickers: Iterable[str] | None = None,
) -> Dict[str, int]:
    """Pull federal contract awards for the given tickers.

    Same defaulting strategy as :func:`ingest_lobbying` — when ``tickers``
    is None, only fetch for tickers we already have transactions on.
    """
    try:
        client = QuiverQuantClient()
    except ValueError as e:
        logger.error("QuiverQuant configuration error: %s", e)
        return {"imported": 0, "duplicates": 0, "errors": 0}

    results = {"imported": 0, "duplicates": 0, "errors": 0}

    if tickers is None:
        from src.db.models import Transaction

        tickers = [
            row[0]
            for row in db.query(Transaction.ticker)
            .filter(Transaction.ticker.isnot(None))
            .distinct()
            .all()
        ]

    for ticker in tickers:
        rows = client.get_government_contracts(ticker=ticker)
        for r in rows:
            try:
                agency = (r.get("Agency") or r.get("AgencyName") or "").strip() or None
                description = (r.get("Description") or "").strip() or None
                amount = _parse_decimal(r.get("Amount") or r.get("Dollars"))
                awarded_date = _parse_date(
                    r.get("Date") or r.get("AwardedDate") or r.get("award_date")
                )
                end_date = _parse_date(r.get("EndDate") or r.get("end_date"))

                existing = (
                    db.query(GovernmentContract)
                    .filter(
                        GovernmentContract.ticker == ticker,
                        GovernmentContract.agency == agency,
                        GovernmentContract.amount == amount,
                        GovernmentContract.awarded_date == awarded_date,
                    )
                    .first()
                )
                if existing:
                    results["duplicates"] += 1
                    continue

                db.add(
                    GovernmentContract(
                        ticker=ticker,
                        agency=agency[:255] if agency else None,
                        description=description,
                        amount=amount,
                        awarded_date=awarded_date,
                        end_date=end_date,
                    )
                )
                results["imported"] += 1
            except Exception:
                logger.exception("Failed to ingest contract row")
                results["errors"] += 1

    db.commit()
    logger.info("Contract ingestion complete: %s", results)
    return results
