"""In-process rate limiting (no Redis: locked decision). Per app instance; reset on restart."""

import ipaddress
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import HardeningSettings


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
            self._windows.pop(key, None)
            self._make_room(now)
            self._windows[key] = (now, 1)  # re-inserted last, so the dict stays ordered by start
            return None
        start, count = entry
        if count >= self._limit:
            return max(1, math.ceil(start + self._window - now))
        self._windows[key] = (start, count + 1)  # same position: the start did not change
        return None

    def _make_room(self, now: float) -> None:
        """Entries are ordered by window start, so expired ones and the oldest are at the front."""
        while self._windows:
            oldest = next(iter(self._windows))
            expired = now - self._windows[oldest][0] >= self._window
            if not expired and len(self._windows) < self._max_keys:
                return
            del self._windows[oldest]


def client_key(host: str | None) -> str:
    """Limiter key for a client address. IPv6 clients are grouped by /64 (one subscriber)."""
    if host is None:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


@dataclass(frozen=True)
class RateLimits:
    """The limiters the API uses. `enabled=False` makes every check a no-op."""

    enabled: bool
    public: RateLimiter  # per client IP: /health and /ready
    api: RateLimiter  # per client IP: every /api/v1 request, before any token or database work
    ticket_create: RateLimiter  # per user: each ticket costs a paid model call
    comment_create: RateLimiter  # per user

    @classmethod
    def from_settings(cls, settings: HardeningSettings) -> "RateLimits":
        def per_minute(limit: int) -> RateLimiter:
            return RateLimiter(limit, 60.0)

        return cls(
            enabled=settings.rate_limit_enabled,
            public=per_minute(settings.rate_limit_public_per_minute),
            api=per_minute(settings.rate_limit_api_per_minute),
            ticket_create=per_minute(settings.rate_limit_ticket_create_per_minute),
            comment_create=per_minute(settings.rate_limit_comment_per_minute),
        )
