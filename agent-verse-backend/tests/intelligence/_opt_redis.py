"""Counter ops for the dict-backed Redis mocks in the self-optimizer tests.

TenantOptimizationState keeps the goal counter in its own key (atomic INCR) and
resets it with SET, so a mock that only implements get/setex needs these too.
"""

from __future__ import annotations

from typing import Any


def add_counter_ops(redis: Any, store: dict[str, Any]) -> Any:
    async def _set(key: str, value: Any, ex: int | None = None, **_kw: Any) -> None:
        store[key] = str(value)

    async def _incr(key: str) -> int:
        store[key] = str(int(float(store.get(key) or 0)) + 1)
        return int(store[key])

    async def _expire(_key: str, _seconds: int) -> bool:
        return True

    redis.set = _set
    redis.incr = _incr
    redis.expire = _expire
    return redis
