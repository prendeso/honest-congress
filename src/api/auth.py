"""Admin authentication helpers for the API."""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from typing import Deque, Dict

from fastapi import Header, HTTPException

from src.config import get_settings

_TOKEN_TTL_SECONDS = 8 * 60 * 60

# Accepted only when ENV is not production. See require_admin().
DEV_TOKEN = "local-dev-token"
_admin_tokens: Dict[str, float] = {}


def _purge_expired_tokens() -> None:
    now = time.time()
    expired = [token for token, expiry in _admin_tokens.items() if expiry <= now]
    for token in expired:
        _admin_tokens.pop(token, None)


def issue_admin_token() -> str:
    _purge_expired_tokens()
    token = secrets.token_urlsafe(32)
    _admin_tokens[token] = time.time() + _TOKEN_TTL_SECONDS
    return token


def revoke_admin_token(token: str) -> None:
    _admin_tokens.pop(token, None)


def validate_admin_token(token: str | None) -> bool:
    if not token:
        return False
    _purge_expired_tokens()
    return token in _admin_tokens


def require_admin(x_admin_token: str | None = Header(None)) -> str:
    settings = get_settings()

    # Local-development convenience. This MUST stay below the settings read and
    # behind the is_production check: without the guard, anyone who knows the
    # literal string gets admin on every mutating endpoint in production.
    if not settings.is_production and x_admin_token == DEV_TOKEN:
        return x_admin_token

    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin password not configured")
    if not validate_admin_token(x_admin_token):
        raise HTTPException(status_code=401, detail="Admin authorization required")
    return x_admin_token


def password_matches(supplied: str, expected: str) -> bool:
    """Compare in constant time, so response timing does not leak the prefix."""
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


# Failed-login throttling. The login route was unthrottled: nothing stopped a
# script guessing ADMIN_PASSWORD as fast as the server would answer.
#
# Two limits, because the per-client one alone is not enough here. The
# container trusts X-Forwarded-For from any peer (FORWARDED_ALLOW_IPS="*" in the
# Dockerfile, which is what makes Railway's HTTPS proxy work), so the client
# address is whatever the caller claims and a guesser can rotate it per
# request. The global limit is the one that cannot be dodged. Its cost is that a
# guesser can lock the real operator out for a window -- acceptable, because
# nothing the site needs depends on admin: the pipeline runs in GitHub Actions.
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_CLIENT = 5
MAX_FAILURES_OVERALL = 50


class LoginThrottle:
    """Count failed logins per client and overall within a sliding window."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._by_client: Dict[str, Deque[float]] = {}
        self._overall: Deque[float] = deque()

    def _evict(self, times: Deque[float], now: float) -> None:
        while times and now - times[0] >= LOGIN_WINDOW_SECONDS:
            times.popleft()

    def retry_after(self, client: str) -> int | None:
        """Seconds until `client` may try again, or None if it may now."""
        now = self._clock()
        with self._lock:
            self._evict(self._overall, now)
            own = self._by_client.get(client)
            if own is not None:
                self._evict(own, now)
                if not own:
                    del self._by_client[client]
                    own = None
            blocking = []
            if own is not None and len(own) >= MAX_FAILURES_PER_CLIENT:
                blocking.append(own[0])
            if len(self._overall) >= MAX_FAILURES_OVERALL:
                blocking.append(self._overall[0])
            if not blocking:
                return None
            return max(1, int(LOGIN_WINDOW_SECONDS - (now - min(blocking))) + 1)

    def record_failure(self, client: str) -> None:
        now = self._clock()
        with self._lock:
            self._by_client.setdefault(client, deque()).append(now)
            self._overall.append(now)

    def reset(self) -> None:
        with self._lock:
            self._by_client.clear()
            self._overall.clear()


login_throttle = LoginThrottle()


def refuse_pipeline_in_production() -> None:
    """Keep the detector pipeline out of the web process in production.

    `/analyze`, `/regenerate`, `/full-refresh` and `/cleanup` rewrite the
    `anomalies` table from inside the web service. In production that table is
    written by GitHub Actions, and `daily-update`, `rebuild` and `maintenance`
    share one concurrency group precisely so that only one of them writes at a
    time -- `persist_anomalies` deduplicates with a SELECT then an INSERT, and
    two interleaved passes can both insert. A button in the admin panel sat
    outside that lock entirely.

    They are also not the same pipeline. None of them runs the purges, the
    significance correction or the percentile ranks that `cli analyze` runs, so
    `/regenerate` in particular wiped every finding and replaced it with ones
    carrying no q-value, which the API then serves as "untested".

    Set ALLOW_ADMIN_PIPELINE_ROUTES=true to re-enable them deliberately.
    """
    settings = get_settings()
    if settings.is_production and not settings.allow_admin_pipeline_routes:
        raise HTTPException(
            status_code=409,
            detail=(
                "Disabled in production: the analysis pipeline runs in GitHub Actions "
                "(Daily Disclosure Update, Rebuild, Maintenance), one writer at a time. "
                "Dispatch one of those instead."
            ),
        )
