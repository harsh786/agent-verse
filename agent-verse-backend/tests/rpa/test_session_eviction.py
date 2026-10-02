"""RPA-03: at the cap only an idle-past-grace session is evicted, and eviction is
complete (closed, deregistered, slot released); a recently used one never is.

Hitting the cap used to close another goal's live browser mid-workflow
(fire-and-forget) and left its Redis record claiming it was live.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rpa.session_manager import BrowserSession, BrowserSessionCapError, BrowserSessionManager
from tests.rpa._lease_redis import LeaseRedis


def _manager(redis: Any, cap: int) -> BrowserSessionManager:
    m = BrowserSessionManager(redis=redis, max_sessions_per_tenant=cap)

    async def _create(sid: str, tenant: str, **_k: Any) -> BrowserSession:
        s = BrowserSession(session_id=sid, tenant_id=tenant)
        s._browser = MagicMock()
        s._browser.close = AsyncMock()
        s._page = MagicMock()
        return s

    m._create_session = _create  # type: ignore[method-assign]
    return m


async def test_recently_used_sessions_are_never_evicted() -> None:
    m = _manager(LeaseRedis(), cap=5)
    for i in range(5):
        await m.get_or_create(f"s{i}", "t1")
    with pytest.raises(BrowserSessionCapError):
        await m.get_or_create("s5", "t1")
    assert all(m._sessions[(f"s{i}", "t1")].is_alive for i in range(5))


async def test_an_idle_session_is_evicted_completely() -> None:
    redis = LeaseRedis()
    m = _manager(redis, cap=2)
    idle = await m.get_or_create("idle", "t1")
    await m.get_or_create("busy", "t1")
    assert await redis.get("rpa_session:t1:idle") is not None
    idle.last_used_at = time.monotonic() - m._evict_idle_s - 5

    new = await m.get_or_create("new", "t1")

    assert new.session_id == "new"
    idle._browser = None  # closed below
    assert ("idle", "t1") not in m._sessions
    assert m._sessions[("busy", "t1")].is_alive
    # The evicted session's registry record and slot are gone.
    assert await redis.get("rpa_session:t1:idle") is None
    assert "idle" not in redis.zsets["rpa:leases:t1"]


async def test_eviction_awaits_the_browser_close() -> None:
    m = _manager(LeaseRedis(), cap=1)
    idle = await m.get_or_create("idle", "t1")
    browser_close = idle._browser.close
    idle.last_used_at = time.monotonic() - m._evict_idle_s - 5
    await m.get_or_create("new", "t1")
    browser_close.assert_awaited_once()
