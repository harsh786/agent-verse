"""Test helper: give a mocked IngestionJobTracker a real (non-renewing) SyncLease.

The worker holds the Source's lock through ``tracker.hold(...)`` (TG-12) and
releases it with ``lease.release()`` -> ``tracker.release_lock(source, tenant,
token)``. On a bare AsyncMock tracker ``hold`` would return a mock lease whose
``check()`` is an un-awaited coroutine.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from app.ingestion.job_tracker import SyncLease


def install_lease(tracker: Any, *, fence: int = 1) -> Any:
    async def _hold(source_id: str, tenant_id: str, token: str, *, ttl_seconds: int) -> SyncLease:
        return SyncLease(tracker, source_id, tenant_id, token, fence, ttl_seconds)

    tracker.hold = AsyncMock(side_effect=_hold)
    return tracker
