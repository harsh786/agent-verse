"""RPA-02: global (Redis) per-tenant browser cap plus a per-host cap; full -> 429.

The per-tenant cap counted only this process's sessions, so a tenant could run 5
browsers on every replica and worker, with no host bound; at the cap it closed
another goal's live browser (or returned a page-less session).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rpa.session_manager import BrowserSession, BrowserSessionCapError, BrowserSessionManager
from app.tenancy.context import PlanTier, TenantContext
from tests.rpa._lease_redis import LeaseRedis


def _live(sid: str, tenant: str) -> BrowserSession:
    s = BrowserSession(session_id=sid, tenant_id=tenant)
    s._browser = MagicMock()
    s._browser.close = AsyncMock()
    s._page = MagicMock()
    s.ssrf_guarded = True
    return s


def _manager(redis: Any, **kw: Any) -> BrowserSessionManager:
    m = BrowserSessionManager(redis=redis, **kw)

    async def _create(sid: str, tenant: str, **_k: Any) -> BrowserSession:
        return _live(sid, tenant)

    m._create_session = _create  # type: ignore[method-assign]
    return m


async def test_tenant_cap_is_global_across_replicas() -> None:
    redis = LeaseRedis()
    a = _manager(redis, max_sessions_per_tenant=2)
    b = _manager(redis, max_sessions_per_tenant=2)
    await a.get_or_create("s1", "t1")
    await b.get_or_create("s2", "t1")
    with pytest.raises(BrowserSessionCapError) as exc:
        await a.get_or_create("s3", "t1")
    assert exc.value.scope == "tenant"
    assert sorted(exc.value.active_sessions) == ["s1", "s2"]
    # Nothing was closed to make room.
    assert a._sessions[("s1", "t1")].is_alive and b._sessions[("s2", "t1")].is_alive
    # Another tenant is unaffected; closing a session frees its slot everywhere.
    await a.get_or_create("x1", "t2")
    await b.close("s2", "t1")
    await a.get_or_create("s3", "t1")


async def test_host_cap_bounds_chromium_processes() -> None:
    m = _manager(LeaseRedis(), max_sessions_per_tenant=5, max_browsers_per_host=2)
    await m.get_or_create("a", "t1")
    await m.get_or_create("b", "t2")
    with pytest.raises(BrowserSessionCapError) as exc:
        await m.get_or_create("c", "t3")
    assert exc.value.scope == "host"


async def test_reusing_an_open_session_never_counts_twice() -> None:
    m = _manager(LeaseRedis(), max_sessions_per_tenant=1)
    first = await m.get_or_create("s1", "t1")
    assert await m.get_or_create("s1", "t1") is first


async def test_redis_outage_degrades_to_a_bounded_local_cap() -> None:
    redis = LeaseRedis()
    redis.fail = True
    m = _manager(redis, max_sessions_per_tenant=1)
    await m.get_or_create("s1", "t1")
    with pytest.raises(BrowserSessionCapError):
        await m.get_or_create("s2", "t1")


async def test_failed_browser_launch_releases_the_slot() -> None:
    redis = LeaseRedis()
    m = BrowserSessionManager(redis=redis, max_sessions_per_tenant=1)
    m._create_session = AsyncMock(side_effect=RuntimeError("launch failed"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await m.get_or_create("s1", "t1")
    assert redis.zsets.get("rpa:leases:t1", {}) == {}


def test_api_answers_429_with_the_active_sessions() -> None:
    from app.api.rpa import router
    from app.rpa.executor import RPAExecutor

    redis = LeaseRedis()
    manager = _manager(redis, max_sessions_per_tenant=1)
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    executor = RPAExecutor(session_manager=manager)
    executor._playwright_available = True
    app.state.rpa_executor = executor
    app.state.rpa_session_manager = manager
    client = TestClient(app)
    import asyncio

    asyncio.run(manager.get_or_create("busy", "t1"))
    resp = client.post(
        "/rpa/execute",
        json={"tool_name": "rpa_extract_text", "arguments": {}, "session_id": "new"},
    )
    assert resp.status_code == 429, resp.text
    body = resp.json()["detail"]
    assert body["code"] == "browser_session_limit"
    assert body["active_sessions"] == ["busy"]
