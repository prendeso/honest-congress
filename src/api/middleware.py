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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline browser hardening on every response.

    No Content-Security-Policy, deliberately: the pages load the Tailwind play
    CDN and Alpine.js, and Alpine evaluates its attribute expressions with
    `new Function`, so a policy that let the site work would need
    'unsafe-eval' and 'unsafe-inline' and would say almost nothing. The headers
    here are the ones that cost nothing to get right.

    HSTS only in production: sent from a local http://localhost it would pin
    the developer's browser to HTTPS for a host that does not serve it.
    """

    def __init__(self, app, *, hsts: bool):
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        # The admin panel is a password form, so nothing off-site may frame it.
        # SAMEORIGIN rather than DENY: the disclosures page probes a filing's
        # PDF in a hidden same-origin iframe before opening it.
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self._hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
