"""ASGI middleware: request IDs and access logging.

Every incoming request is tagged with a UUID4 (or honors an inbound
``X-Request-ID`` header from a load balancer / reverse proxy) and we log
one access line per request with method, path, status, and duration. The
request ID is echoed back on the response and attached to the logging
context so application logs can be correlated.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Anywhere in the codebase you can do
#   from src.api.middleware import request_id_var
#   request_id = request_id_var.get()
# to surface the current request's ID in a log line.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Tag every request with an X-Request-ID and emit one access log line."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_failed method=%s path=%s elapsed_ms=%.1f request_id=%s",
                request.method,
                request.url.path,
                elapsed_ms,
                request_id,
            )
            request_id_var.reset(token)
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        # /health hits don't deserve a log line each — they fire on a
        # schedule and would drown out signal.
        if not request.url.path.startswith("/health"):
            logger.info(
                "request method=%s path=%s status=%d elapsed_ms=%.1f request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
        request_id_var.reset(token)
        return response
