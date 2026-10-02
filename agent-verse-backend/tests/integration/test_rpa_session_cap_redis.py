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


async def test_dead_owner_is_reclaimed_after_its_liveness_key_expires(redis_url: str) -> None:
    """RPA-04: owner A dies (its liveness key lapses); B may open the session."""
    import asyncio

    import redis.asyncio as aioredis

    from app.rpa import session_manager as sm
    from app.rpa.session_manager import SessionOnAnotherReplicaError

    ra, rb = aioredis.from_url(redis_url), aioredis.from_url(redis_url)
    try:
        a, b = _manager(ra), _manager(rb)
        await a.get_or_create("s1", "tenant-dead")
        with pytest.raises(SessionOnAnotherReplicaError):
            await b.get_or_create("s1", "tenant-dead")
        # A crashes: nothing refreshes its liveness key; let it expire for real.
        await ra.expire(f"rpa_replica:{a.replica_id}:alive", 1)
        await asyncio.sleep(1.5)
        assert sm._REPLICA_ALIVE_TTL_S > 1
        session = await b.get_or_create("s1", "tenant-dead")
        assert session.is_alive
        await b.close_all()
    finally:
        await ra.aclose()
        await rb.aclose()
