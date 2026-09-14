"""Shared HTTP machinery for the official-source API clients.

Every quota this has to respect is a *count per window*, not a rate: api.data.gov
allows 1,000 requests per hour, Congress.gov 20,000, and the Senate LDA roughly
15 per minute anonymously (120 with a key). That distinction is the whole point
of :class:`RateLimiter`. Spacing requests evenly -- one every 3.6 seconds for the
FEC -- would turn a 200-request run into thirteen minutes for no reason at all.
Here the burst is free and only the request that would actually cross the line
waits, which is how the servers themselves count.

:class:`ThrottledClient` carries the rest: the retry loop, `Retry-After`, and an
optional per-run request budget. Three clients now need identical behaviour
there, and three copies of a retry loop is how they drift apart.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Tuple

import requests

logger = logging.getLogger(__name__)

HOUR = 3600
MINUTE = 60

MAX_RETRIES = 4
DEFAULT_BACKOFF_SECONDS: Tuple[int, ...] = (2, 4, 8, 16)

# Retried on the same backoff as a 429, because they mean the same thing to a
# caller: the service did not answer this time, and might next time.
#
# This is not a theoretical list. A single 522 from Congress.gov killed a
# production sponsorship ingest outright:
#
#     requests.exceptions.HTTPError: 522 Server Error: status code 522 for url:
#     https://api.congress.gov/v3/member/D000243/sponsored-legislation
#
# It was raised on the first attempt with no retry at all -- the loop below only
# ever retried 429 -- and it aborted the sweep for every member after that one.
# The workflow step carries `continue-on-error: true`, so the run reported
# success and two detectors sat empty.
#
# 520-524 are Cloudflare's origin errors and every one of these services sits
# behind a CDN: 520 unknown, 521 origin down, 522 connection timed out, 523
# origin unreachable, 524 origin timed out. 502/503/504 are the plain gateway
# equivalents. 500 is deliberately NOT here: a genuine server-side bug repeated
# four times is four times the load for the same answer.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504, 520, 521, 522, 523, 524})

# The same idea one layer down. A status code only exists if a response arrived;
# these are the failures that happen BEFORE one does, and the retry loop below
# branched on status, so it never saw them at all.
#
# Also not hypothetical. A read timeout to SEC killed the industry sync outright:
#
#     requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='www.sec.gov',
#     port=443): Read timed out. (read timeout=45)
#
# One slow response out of roughly a thousand, on a step whose whole job is to
# cache a value per ticker, and the step died and reported success under
# `continue-on-error`. Fixing the transient STATUSES without the transient
# EXCEPTIONS left exactly half the hole open.
#
# `requests.RequestException` is deliberately NOT the whole net: an HTTPError
# raised by `raise_for_status` is a real answer from the server and must not be
# retried, and `TooManyRedirects` or a malformed URL will not fix themselves.
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)
DEFAULT_TIMEOUT = 60


class RequestBudgetExhausted(RuntimeError):
    """Raised when a run hits its own `max_requests` cap.

    Not an error condition: a capped run is expected to stop early and be
    resumed. The caller catches this and reports what it managed to ingest.
    """


class RateLimiter:
    """Allow `limit` calls per `window_seconds`, bursting freely within it."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = HOUR,
        *,
        name: str = "API",
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.name = name
        self._clock = clock
        self._sleeper = sleeper
        self._times: Deque[float] = deque()

    def _evict(self, now: float) -> None:
        while self._times and now - self._times[0] >= self.window_seconds:
            self._times.popleft()

    def acquire(self) -> None:
        now = self._clock()
        self._evict(now)

        if len(self._times) >= self.limit:
            wait = self.window_seconds - (now - self._times[0])
            # A per-second limiter fills its window constantly and by design;
            # a per-hour one filling up is worth knowing about. Log on the size
            # of the wait rather than on the fact of it, or a routine run buries
            # the real warnings under hundreds of sub-second ones.
            if wait >= 1:
                logger.warning("%s quota reached; waiting %.0fs", self.name, wait)
            else:
                logger.debug("%s pacing; waiting %.2fs", self.name, wait)
            self._sleeper(wait)
            now = self._clock()
            self._evict(now)

        self._times.append(now)


class ThrottledClient:
    """Base for the official-source API clients.

    Subclasses set `base_url` and `name`, and override `_auth_params` or
    `_auth_headers` depending on how their service wants to be identified --
    FEC takes a query parameter, the Senate LDA an Authorization header,
    Congress.gov a query parameter again. Everything else is the same for all
    three, which is exactly why it lives here.
    """

    base_url: str = ""
    name: str = "API"

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = HOUR,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_requests: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        backoff_seconds: Tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        self.session = session or requests.Session()
        self.requests_made = 0
        self.max_requests = max_requests
        self._timeout = timeout
        self._sleeper = sleeper
        self._backoff = backoff_seconds
        self._limiter = RateLimiter(
            limit, window_seconds, name=self.name, clock=clock, sleeper=sleeper
        )

    def _auth_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return params

    def _auth_headers(self) -> Dict[str, str]:
        return {}

    def get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Fetch and decode JSON. Most of these APIs speak JSON; one does not."""
        payload: Dict[str, Any] = self._request(path, params).json()
        return payload

    def post(self, path: str, json: Any = None) -> Dict[str, Any]:
        """POST a JSON body and decode the JSON reply.

        Same throttling, same retries, same request budget as `get`. USASpending
        is the only caller: its search endpoint takes the whole query as a body.
        """
        payload: Dict[str, Any] = self._request(path, method="POST", json=json).json()
        return payload

    def _request(
        self,
        path: str,
        params: Dict[str, Any] | None = None,
        *,
        method: str = "GET",
        json: Any = None,
    ) -> requests.Response:
        """The throttled, retried request itself, decoded by the caller.

        Separate from `get` because EDGAR's browse endpoint serves atom XML, and
        the rate limiting and 429 handling should not have to be written twice
        to accommodate that.

        `method` and `json` are keyword-only and trailing on purpose:
        `EdgarCompanyClient.get_text` calls this positionally as
        `self._request(path, params)`, and the four subclasses were written
        against that shape.

        POST exists here so USASpending can stop being the one feed outside all
        of this. It needs POST, this client only spoke GET, so it grew its own
        retry loop -- and two rounds of work on transient failures passed it by
        while it kept dying on the first 502. Everything below the transport
        line is method-agnostic and always was.
        """
        if self.max_requests is not None and self.requests_made >= self.max_requests:
            raise RequestBudgetExhausted(
                f"stopped after {self.requests_made} requests (--max-requests)"
            )

        query = self._auth_params(dict(params or {}))
        headers = self._auth_headers()

        last_failure: str | None = None
        for attempt in range(MAX_RETRIES):
            self._limiter.acquire()
            self.requests_made += 1

            try:
                if method == "GET":
                    # Unchanged, deliberately: the existing tests assert on
                    # `session.get.call_args`, so this call's shape is a
                    # contract, not an implementation detail.
                    response = self.session.get(
                        f"{self.base_url}{path}",
                        params=query,
                        headers=headers,
                        timeout=self._timeout,
                    )
                elif method == "POST":
                    response = self.session.post(
                        f"{self.base_url}{path}",
                        json=json,
                        headers=headers or None,
                        timeout=self._timeout,
                    )
                else:  # pragma: no cover - no caller needs another verb yet
                    raise ValueError(f"unsupported method {method!r}")
            except RETRYABLE_EXCEPTIONS as exc:
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                logger.warning(
                    "%s did not answer (%s); retrying in %ss (attempt %d of %d)",
                    self.name,
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                self._sleeper(delay)
                last_failure = type(exc).__name__
                continue

            if response.status_code in RETRYABLE_STATUS:
                # These services send Retry-After on throttle. Honour it when
                # present; the backoff table is only a fallback for when it is
                # not, and guessing shorter than the server asked for is how a
                # throttle turns into a ban.
                retry_after = response.headers.get("Retry-After")
                delay: float = self._backoff[min(attempt, len(self._backoff) - 1)]
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        pass
                logger.warning(
                    "%s returned %s; retrying in %ss (attempt %d of %d)",
                    self.name,
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                self._sleeper(delay)
                last_failure = str(response.status_code)
                continue

            response.raise_for_status()
            return response

        raise requests.exceptions.RetryError(
            f"{self.name} still failing with {last_failure} after {MAX_RETRIES} attempts"
        )
