"""Pure helpers extracted from `orchestrator.py`.

Each function here takes its inputs explicitly (no `self`) and produces a
result. Keeping these standalone makes them trivial to unit test and gives
us obvious targets when the larger `IngestionOrchestrator` is decomposed
per-source.

`IngestionOrchestrator` still exposes wrappers on these so external
callers (CLI, API admin routes) don't need to change.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.models import Disclosure

logger = logging.getLogger(__name__)


def fix_future_dates(db: Session) -> int:
    """Clamp filing_date on any disclosure dated in the future to ``now``.

    Sometimes the source feeds carry typos like 2099-01-01. Returns the
    number of rows fixed. Idempotent: a second call yields 0.
    """
    now = datetime.now()
    future_disclosures = db.query(Disclosure).filter(Disclosure.filing_date > now).all()

    fixed = 0
    for disclosure in future_disclosures:
        old_date = disclosure.filing_date
        disclosure.filing_date = now
        logger.info("Fixed future date: %s %s -> %s", disclosure.document_id, old_date, now)
        fixed += 1

    if fixed > 0:
        db.commit()
        logger.info("Fixed %d disclosures with future dates", fixed)

    return fixed


def build_alt_pdf_url(disclosure: Disclosure) -> str | None:
    """Try an alternative PDF URL pattern if the primary URL 404s.

    The House Clerk's PDF index sometimes points at
    `/financial-pdfs/<year>/<doc>.pdf` while the file actually lives at
    `/financial-pdfs/<doc>.pdf` (no year segment). This helper produces
    the year-stripped variant for the caller to fall back to.
    """
    if not disclosure.document_url:
        return None

    if (
        "/financial-pdfs/" in disclosure.document_url
        and "/ptr-pdfs/" not in disclosure.document_url
    ):
        parts = disclosure.document_url.split("/financial-pdfs/")
        if len(parts) == 2:
            tail = parts[1]
            if "/" in tail:
                doc_name = tail.split("/", 1)[1]
                return f"{parts[0]}/financial-pdfs/{doc_name}"
    return None


def record_failed_download(data_dir: Path, disclosure: Disclosure, reason: str) -> str:
    """Append failed-download details to ``data_dir/failed_downloads.csv``.

    Creates the file with a header row on first call. Returns ``reason``
    so callers can chain the assignment to ``disclosure.parse_error``.
    """
    failed_path = data_dir / "failed_downloads.csv"
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not failed_path.exists()

    with open(failed_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(
                ["timestamp", "document_id", "filing_year", "is_ptr", "document_url", "reason"]
            )
        writer.writerow(
            [
                datetime.utcnow().isoformat(),
                disclosure.document_id,
                disclosure.filing_year,
                disclosure.is_ptr,
                disclosure.document_url,
                reason,
            ]
        )

    logger.error("Failed to download %s: %s", disclosure.document_url, reason)
    return reason
