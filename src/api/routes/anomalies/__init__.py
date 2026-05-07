"""Aggregator for the anomalies sub-routers.

The original `anomalies.py` was a single 700-line file mixing read,
analyze, admin, and background-sync endpoints. We split it by concern.
The order of `include_router` calls below is significant — FastAPI
resolves routes in registration order, so the catchall ``/{anomaly_id}``
in `query.py` must come AFTER the static admin/detect paths.
"""

from fastapi import APIRouter

from src.api.routes.anomalies.admin import router as admin_router
from src.api.routes.anomalies.detect import router as detect_router
from src.api.routes.anomalies.query import router as query_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(detect_router)
router.include_router(query_router)

__all__ = ["router"]
