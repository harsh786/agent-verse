"""Regression: the executor's dedup cache must honour the sync content-hash
contract even when Redis is available.

Silent wiring disconnect (found via the e2e goal-lifecycle harness): when Redis
is present, ``_build_dedup_cache`` returned a ``RedisDeduplicationCache`` — whose
API is the *goal-submission* dedup (async ``get_existing``/``register``), not the
executor's *tool-call* content-hash dedup (sync ``is_duplicate``/``mark_seen``).
The executor calls ``self._dedup_cache.is_duplicate(...)`` synchronously
(``executor_mixin.py:608``), so in production every goal crashed on its first
step with ``'RedisDeduplicationCache' object has no attribute 'is_duplicate'``.
Unit tests never caught it because they use the in-memory cache (no Redis).
"""

from __future__ import annotations

from app.services.goal_service import _build_dedup_cache
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t-dedup", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def test_dedup_cache_without_redis_supports_executor_contract() -> None:
    cache = _build_dedup_cache(None)
    tenant = _tenant()
    assert cache.is_duplicate(content_hash="h1", tenant_ctx=tenant) is False
    cache.mark_seen(content_hash="h1", tenant_ctx=tenant)
    assert cache.is_duplicate(content_hash="h1", tenant_ctx=tenant) is True


def test_dedup_cache_with_redis_still_supports_executor_contract() -> None:
    """The bug: a non-None redis made this return a cache with no is_duplicate."""

    class _FakeRedis:
        pass

    cache = _build_dedup_cache(_FakeRedis())
    tenant = _tenant()
    # The executor calls these synchronously — they MUST exist and behave.
    assert hasattr(cache, "is_duplicate"), (
        "executor dedup cache missing is_duplicate — goals crash on first step"
    )
    assert hasattr(cache, "mark_seen")
    assert cache.is_duplicate(content_hash="h2", tenant_ctx=tenant) is False
    cache.mark_seen(content_hash="h2", tenant_ctx=tenant)
    assert cache.is_duplicate(content_hash="h2", tenant_ctx=tenant) is True


def test_is_duplicate_is_synchronous_not_coroutine() -> None:
    """A coroutine return would be truthy → every step wrongly skipped."""
    import inspect

    cache = _build_dedup_cache(object())
    tenant = _tenant()
    result = cache.is_duplicate(content_hash="h3", tenant_ctx=tenant)
    assert not inspect.iscoroutine(result)
    assert result is False
