"""A rolling-window request limiter, shared by the official-source clients.

Both quotas this has to respect are *counts per window*, not rates: api.data.gov
allows 1,000 requests per hour, and the Senate LDA allows roughly 15 per minute
anonymously (120 with a key). That distinction is the whole point of the
implementation. Spacing requests evenly -- one every 3.6 seconds for the FEC --
would turn a 200-request run into thirteen minutes for no reason at all. Here
the burst is free and only the request that would actually cross the line waits,
which is how the servers themselves count.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable, Deque

logger = logging.getLogger(__name__)

HOUR = 3600
MINUTE = 60


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
            logger.warning("%s quota reached; waiting %.0fs", self.name, wait)
            self._sleeper(wait)
            now = self._clock()
            self._evict(now)

        self._times.append(now)
