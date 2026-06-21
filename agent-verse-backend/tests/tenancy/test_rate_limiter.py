"""Tests for SlidingWindowRateLimiter."""


from app.tenancy.rate_limiter import SlidingWindowRateLimiter
from app.tenancy.store import TenantScopedStore
from tests.tenancy.test_store import FakeRedis


def _store() -> TenantScopedStore:
    return TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]


async def test_first_request_is_allowed() -> None:
    limiter = SlidingWindowRateLimiter(_store(), window_seconds=60)
    allowed, remaining, _ = await limiter.check_and_record("endpoint", limit=5, now=1000.0)
    assert allowed is True
    assert remaining == 4


async def test_requests_under_limit_are_allowed() -> None:
    store = _store()
    limiter = SlidingWindowRateLimiter(store, window_seconds=60)
    for i in range(5):
        allowed, _, _ = await limiter.check_and_record("ep", limit=5, now=float(i))
    assert allowed is True


async def test_request_at_limit_is_blocked() -> None:
    store = _store()
    limiter = SlidingWindowRateLimiter(store, window_seconds=60)
    for _ in range(5):
        await limiter.check_and_record("ep", limit=5, now=1000.0)
    allowed, remaining, _ = await limiter.check_and_record("ep", limit=5, now=1000.5)
    assert allowed is False
    assert remaining == 0


async def test_sliding_window_expires_old_entries() -> None:
    store = _store()
    limiter = SlidingWindowRateLimiter(store, window_seconds=60)
    # Fill up limit at t=0
    for _ in range(5):
        await limiter.check_and_record("ep", limit=5, now=0.0)
    # 61 seconds later, old entries are outside the window
    allowed, _, _ = await limiter.check_and_record("ep", limit=5, now=61.0)
    assert allowed is True


async def test_remaining_decreases_monotonically() -> None:
    store = _store()
    limiter = SlidingWindowRateLimiter(store, window_seconds=60)
    remainders = []
    for i in range(4):
        _, remaining, _ = await limiter.check_and_record("ep", limit=5, now=float(i))
        remainders.append(remaining)
    assert remainders == sorted(remainders, reverse=True)


async def test_reset_time_is_in_future() -> None:
    limiter = SlidingWindowRateLimiter(_store(), window_seconds=60)
    _, _, reset_at = await limiter.check_and_record("ep", limit=5, now=1000.0)
    assert reset_at > 1000.0


async def test_different_endpoints_have_separate_counters() -> None:
    store = _store()
    limiter = SlidingWindowRateLimiter(store, window_seconds=60)
    for _ in range(5):
        await limiter.check_and_record("ep1", limit=5, now=1000.0)
    # ep2 should still be under limit
    allowed, _, _ = await limiter.check_and_record("ep2", limit=5, now=1000.0)
    assert allowed is True
