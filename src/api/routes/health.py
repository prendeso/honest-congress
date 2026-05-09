"""Health check endpoints.

* ``/health`` is the deep readiness probe used by Railway's healthcheck — it
  verifies the database connection so a stale-but-running app fails over
  rather than silently 5xx-ing every business request.
* ``/health/live`` is the liveness probe — always returns 200 if the process
  is up. Use this when you want to distinguish "process alive" from "ready
  to serve traffic".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.db import get_db_session

router = APIRouter()


@router.get("/health")
async def health_check(db: Session = Depends(get_db_session)):
    """Readiness probe: 200 when the DB is reachable, 503 otherwise."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=503,
            detail={"status": "unhealthy", "database": "disconnected", "error": str(e)},
        ) from e
    return {"status": "healthy", "database": "connected"}


@router.get("/health/live")
async def liveness_check():
    """Liveness probe: returns 200 as long as the process is responding."""
    return {"status": "alive"}
