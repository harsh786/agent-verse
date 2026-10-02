"""RPA-02 against real Redis: two session managers (two replicas) jointly enforce
the tenant's browser cap."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rpa.session_manager import BrowserSession, BrowserSessionCapError, BrowserSessionManager

pytestmark = pytest.mark.integration


def _manager(redis: Any) -> BrowserSessionManager:
    m = BrowserSessionManager(redis=redis, max_sessions_per_tenant=2)

    async def _create(sid: str, tenant: str, **_k: Any) -> BrowserSession:
        s = BrowserSession(session_id=sid, tenant_id=tenant)
        s._browser = MagicMock()
        s._browser.close = AsyncMock()
        return s

    m._create_session = _create  # type: ignore[method-assign]
    return m


async def test_two_managers_share_the_tenant_cap(redis_url: str) -> None:
    import redis.asyncio as aioredis

    ra, rb = aioredis.from_url(redis_url), aioredis.from_url(redis_url)
    try:
        a, b = _manager(ra), _manager(rb)
        await a.get_or_create("s1", "tenant-cap")
        await b.get_or_create("s2", "tenant-cap")
        with pytest.raises(BrowserSessionCapError) as exc:
            await a.get_or_create("s3", "tenant-cap")
        assert sorted(exc.value.active_sessions) == ["s1", "s2"]
        await b.close("s2", "tenant-cap")
        await a.get_or_create("s3", "tenant-cap")
        await a.close_all()
        await b.close_all()
    finally:
        await ra.aclose()
        await rb.aclose()
