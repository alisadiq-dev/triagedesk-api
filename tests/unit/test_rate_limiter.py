import pytest

from app.core.rate_limit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(limit: int = 3, window: float = 60.0, max_keys: int = 100) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(limit, window, clock=clock, max_keys=max_keys), clock


def test_requests_up_to_the_limit_are_allowed() -> None:
    limiter, _ = make(limit=3)

    assert [limiter.check("a") for _ in range(3)] == [None, None, None]


def test_the_next_request_is_refused_with_a_retry_after_inside_the_window() -> None:
    limiter, clock = make(limit=2, window=60)
    limiter.check("a")
    clock.now += 10
    limiter.check("a")

    retry_after = limiter.check("a")

    assert retry_after == 50


def test_retry_after_is_at_least_one_second() -> None:
    limiter, clock = make(limit=1, window=60)
    limiter.check("a")
    clock.now += 59.9

    assert limiter.check("a") == 1


def test_the_window_resets() -> None:
    limiter, clock = make(limit=1, window=60)
    limiter.check("a")
    assert limiter.check("a") is not None

    clock.now += 60

    assert limiter.check("a") is None


def test_keys_do_not_affect_each_other() -> None:
    limiter, _ = make(limit=1)
    limiter.check("a")

    assert limiter.check("b") is None
    assert limiter.check("a") is not None


def test_a_refused_request_does_not_extend_the_block() -> None:
    limiter, clock = make(limit=1, window=60)
    limiter.check("a")
    clock.now += 30
    for _ in range(100):
        limiter.check("a")

    clock.now += 30

    assert limiter.check("a") is None


def test_expired_keys_are_dropped_so_memory_stays_bounded() -> None:
    limiter, clock = make(limit=1, window=60, max_keys=10)
    for index in range(10):
        limiter.check(f"old-{index}")
    clock.now += 61

    for index in range(10):
        limiter.check(f"new-{index}")

    assert limiter.tracked_keys <= 10


def test_a_flood_of_distinct_live_keys_cannot_grow_memory_without_bound() -> None:
    limiter, _ = make(limit=1, window=60, max_keys=10)

    for index in range(1000):
        limiter.check(f"key-{index}")

    assert limiter.tracked_keys <= 10


@pytest.mark.parametrize(("limit", "window"), [(0, 60.0), (-1, 60.0), (1, 0.0)])
def test_invalid_settings_are_rejected(limit: int, window: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        RateLimiter(limit, window)


def test_eviction_when_full_does_not_rebuild_the_table_and_drops_the_oldest_first() -> None:
    limiter, clock = make(limit=1, window=60, max_keys=3)
    for name in ("a", "b", "c"):
        limiter.check(name)
        clock.now += 1

    limiter.check("d")  # table full of live keys: the oldest ("a") goes

    assert limiter.check("b") is not None  # still tracked and limited
    assert limiter.check("a") is None  # forgotten, starts a fresh window
