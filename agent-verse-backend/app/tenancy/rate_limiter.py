"""Sliding-window rate limiter backed by TenantScopedStore (Redis sorted sets).

Each request is recorded as a member of a sorted set, scored by timestamp.
Old members outside the window are pruned on every check — no Lua script
needed, no server-side atomicity required for this accuracy level.
"""

from __future__ import annotations

import time
import uuid

from app.tenancy.store import TenantScopedStore


class SlidingWindowRateLimiter:
    """Per-tenant, per-endpoint sliding-window rate limiter.

    Args:
        store: Tenant-scoped Redis store.
        window_seconds: Length of the sliding window (default 60s = per-minute).
    """

    def __init__(self, store: TenantScopedStore, *, window_seconds: int = 60) -> None:
        self._store = store
        self._window = window_seconds

    async def check_and_record(
        self,
        endpoint: str,
        *,
        limit: int,
        now: float | None = None,
    ) -> tuple[bool, int, float]:
        """Check if a request is within rate limits and record it if allowed.

        Returns:
            (allowed, remaining, reset_at_epoch_seconds)
        """
        ts = now if now is not None else time.time()
        window_start = ts - self._window
        key = f"rl:{endpoint}"

        # Prune entries that have slid out of the window
        await self._store.zremrangebyscore(key, 0.0, window_start)

        count = await self._store.zcard(key)
        reset_at = ts + self._window

        if count >= limit:
            return False, 0, reset_at

        # Record this request using a unique member (ts:random) to handle concurrent requests
        member = f"{ts}:{uuid.uuid4().hex}"
        await self._store.zadd(key, {member: ts})
        await self._store.expire(key, self._window * 2)

        return True, limit - count - 1, reset_at
