"""Per-tenant bulkhead semaphores for concurrent tool call limits."""
from __future__ import annotations

import asyncio


class BulkheadRegistry:
    """Per-tenant asyncio.Semaphore to prevent one tenant monopolizing workers.

    Each tenant gets an isolated semaphore. A runaway tenant cannot consume
    all concurrent tool call slots.
    """

    def __init__(self, default_max_concurrent: int = 20) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._default_max = default_max_concurrent
        self._limits: dict[str, int] = {}

    def configure_tenant(self, tenant_id: str, max_concurrent: int) -> None:
        """Set per-tenant concurrency limit."""
        self._limits[tenant_id] = max_concurrent
        # Reset semaphore if limit changed
        self._semaphores.pop(tenant_id, None)

    def get(self, tenant_id: str) -> asyncio.Semaphore:
        """Get or create semaphore for tenant."""
        if tenant_id not in self._semaphores:
            limit = self._limits.get(tenant_id, self._default_max)
            self._semaphores[tenant_id] = asyncio.Semaphore(limit)
        return self._semaphores[tenant_id]

    def available_slots(self, tenant_id: str) -> int:
        """How many concurrent calls this tenant can still make."""
        sem = self._semaphores.get(tenant_id)
        if sem is None:
            return self._limits.get(tenant_id, self._default_max)
        return sem._value  # type: ignore[attr-defined]
