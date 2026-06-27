"""Per-tenant bulkhead semaphores for concurrent tool call limits."""
from __future__ import annotations

import asyncio


class Bulkhead:
    """Async context manager that tracks concurrency without reading private semaphore state."""

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._active = 0  # track ourselves instead of reading private _value attr

    async def __aenter__(self) -> "Bulkhead":
        await self._sem.acquire()
        self._active += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self._active -= 1
        self._sem.release()

    def available_slots(self) -> int:
        return self._max - self._active  # no private attr access


class BulkheadRegistry:
    """Per-tenant asyncio.Semaphore to prevent one tenant monopolizing workers.

    Each tenant gets an isolated semaphore. A runaway tenant cannot consume
    all concurrent tool call slots.
    """

    def __init__(self, default_max_concurrent: int = 20) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._default_max = default_max_concurrent
        self._limits: dict[str, int] = {}
        # Tracked active counts per tenant (avoids accessing sem._value)
        self._active_counts: dict[str, int] = {}

    def configure_tenant(self, tenant_id: str, max_concurrent: int) -> None:
        """Set per-tenant concurrency limit."""
        self._limits[tenant_id] = max_concurrent
        # Reset semaphore if limit changed
        self._semaphores.pop(tenant_id, None)
        self._active_counts.pop(tenant_id, None)

    def get(self, tenant_id: str) -> asyncio.Semaphore:
        """Get or create semaphore for tenant."""
        if tenant_id not in self._semaphores:
            limit = self._limits.get(tenant_id, self._default_max)
            self._semaphores[tenant_id] = asyncio.Semaphore(limit)
        return self._semaphores[tenant_id]

    def available_slots(self, tenant_id: str) -> int:
        """How many concurrent calls this tenant can still make."""
        if tenant_id not in self._semaphores:
            return self._limits.get(tenant_id, self._default_max)
        limit = self._limits.get(tenant_id, self._default_max)
        active = self._active_counts.get(tenant_id, 0)
        return max(0, limit - active)
