"""In-process rate limiting (no Redis: locked decision). Per app instance; reset on restart."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


class RateLimiter:
    """Fixed-window counter per key. Memory is bounded by `max_keys`.

    A fixed window allows a burst of up to twice the limit across a window boundary; that is
    accepted for simplicity. When the table is full of live keys, the oldest key is dropped (its
    counter resets).
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        if limit <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("limit, window and max_keys must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._windows: dict[str, tuple[float, int]] = {}  # key -> (window start, count)

    @property
    def tracked_keys(self) -> int:
        return len(self._windows)

    def check(self, key: str) -> int | None:
        """None when the request is allowed (and counted); otherwise whole seconds to wait."""
        now = self._clock()
        entry = self._windows.get(key)
        if entry is None or now - entry[0] >= self._window:
            self._make_room(now, key)
            self._windows[key] = (now, 1)
            return None
        start, count = entry
        if count >= self._limit:
            return max(1, math.ceil(start + self._window - now))
        self._windows[key] = (start, count + 1)
        return None

    def _make_room(self, now: float, key: str) -> None:
        self._windows.pop(key, None)
        if len(self._windows) < self._max_keys:
            return
        self._windows = {k: v for k, v in self._windows.items() if now - v[0] < self._window}
        while len(self._windows) >= self._max_keys:
            del self._windows[next(iter(self._windows))]


@dataclass(frozen=True)
class RateLimits:
    """The limiters the API uses. `enabled=False` makes every check a no-op."""

    enabled: bool
    public: RateLimiter  # per client IP: /health and /ready
    api: RateLimiter  # per client IP: every /api/v1 request, before any token or database work
    ticket_create: RateLimiter  # per user: each ticket costs a paid model call
    comment_create: RateLimiter  # per user
