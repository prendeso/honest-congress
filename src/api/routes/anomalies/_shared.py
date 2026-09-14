"""Schemas, sync state, and helpers shared across the anomalies sub-routers."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict

# ---------------- response schemas ----------------


class AnomalyResponse(BaseModel):
    """Anomaly response schema."""

    id: int
    member_id: int
    member_name: str
    member_party: str
    member_state: str
    # Both handlers emit None when a member has no chamber recorded, so the
    # schema has to allow it -- as declared, that raised a validation error.
    member_chamber: str | None
    member_in_office: bool
    anomaly_type: str
    severity: str
    title: str
    description: str
    computed_value: float | None
    threshold_value: float | None
    detected_at: datetime
    reviewed: bool
    disclosure_id: int | None
    transaction_id: int | None
    filing_year: int | None
    # Where this finding sits among others of its own type, 0-100.
    # Null when the population was too small to rank against.
    percentile_rank: float | None = None
    # How often chance alone produces a coincidence this strong, and the same
    # after correcting for every test in the run.
    #
    # Null means UNTESTED, and never that the finding passed. It covers two
    # different situations, in `src.analysis.significance`'s own words: "a
    # magnitude rule with no null to shuffle, or testable in principle but not
    # in this run -- too short a span, no eligible trades."
    p_value: float | None = None
    q_value: float | None = None
    # Which of those two a null q_value is: a property of the DETECTOR, not of
    # this finding. It is served from the same declaration `/api/anomalies/types`
    # reads, because the two contradicting each other about the same detector is
    # exactly what happened when this was inferred from `q_value is not None` --
    # `/types` said contract_front_run was tested while every finding of that
    # type said it was not.
    #
    # A finding that was tested and FAILED does not appear here as a null. It
    # carries a q_value above alpha, and the list endpoint omits it by default.
    has_null_model: bool = False

    model_config = ConfigDict(from_attributes=True)


class AnomalyListResponse(BaseModel):
    """Paginated list of anomalies."""

    total: int
    page: int
    page_size: int
    anomalies: List[AnomalyResponse]


class AnomalySummaryResponse(BaseModel):
    """Summary of anomalies by type and severity."""

    total_anomalies: int
    by_type: dict
    by_severity: dict
    by_party: dict
    by_chamber: dict


# ---------------- large-trade sync state ----------------

# One-time per-process flag to avoid repeating the sync work on every request.
# The large-trade sync that used to run here is gone. It fired on three GET
# endpoints, wrote rows during a read, and never committed them; the writes were
# removed and `cli recount` / `cli analyze` own that work now. The function
# outlived its callers, which is how dead code comes to look like a feature.


# ---------------- background sync status ----------------

# Shared progress tracker for the long-running /sync-* and /full-refresh
# endpoints. Pages can poll /api/anomalies/sync-status for updates.
sync_status: Dict[str, Any] = {
    "running": False,
    "operation": None,
    "progress": 0,
    "total": 0,
    "message": "",
    "started_at": None,
    "completed_at": None,
    "result": None,
}
sync_lock = threading.Lock()
