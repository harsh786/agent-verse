"""RPA browser sessions are per-replica: say so instead of pretending.

A live Playwright page exists only in the API process that opened it, but the
session registry is shared (Redis). A request for that session landing on
another replica used to silently launch a brand-new blank browser under the
same id (/rpa/execute) or answer a vague 404 (screenshot / current-view). It now
fails with 409 naming the situation, and the agent path gets an honest error.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rpa import router as rpa_router
from app.rpa.executor import RPAExecutor
from app.rpa.session_manager import (
    BrowserSession,
    BrowserSessionManager,
    SessionOnAnotherReplicaError,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rep", plan=PlanTier.PROFESSIONAL, api_key_id="kid-r")
_KEY = "av_test_rpa_replica"


class _FakeRedis:
    """The shared registry both replicas see."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]


def _live(manager: BrowserSessionManager, sid: str, tenant: str) -> BrowserSession:
    session = BrowserSession(session_id=sid, tenant_id=tenant)
    session._browser = MagicMock()
    session._page = MagicMock()
    session.ssrf_guarded = True
    return session


async def _open_on(manager: BrowserSessionManager, sid: str, tenant: str) -> None:
    manager._create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=_live(manager, sid, tenant)
    )
    await manager.get_or_create(sid, tenant)


def _pair() -> tuple[BrowserSessionManager, BrowserSessionManager, _FakeRedis]:
    redis = _FakeRedis()
    return BrowserSessionManager(redis=redis), BrowserSessionManager(redis=redis), redis


async def test_registry_records_the_owning_replica() -> None:
    a, _b, redis = _pair()
    await _open_on(a, "s1", "t1")
    record = json.loads(redis.data["rpa_session:t1:s1"])
    assert record["replica_id"] == a.replica_id


async def test_other_replica_refuses_instead_of_opening_a_blank_browser() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    b._create_session = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(SessionOnAnotherReplicaError) as exc_info:
        await b.get_or_create("s1", "t1")

    b._create_session.assert_not_called()
    assert exc_info.value.owner_replica == a.replica_id
    assert await b.live_elsewhere("s1", "t1") == a.replica_id
    assert await a.live_elsewhere("s1", "t1") is None


async def test_owning_replica_and_unknown_sessions_are_unaffected() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    assert (await a.get_or_create("s1", "t1")).is_alive
    # A different tenant's id, or a fresh id, is not "elsewhere".
    assert await b.live_elsewhere("s1", "other-tenant") is None
    assert await b.live_elsewhere("fresh", "t1") is None


async def test_closed_session_is_released_for_every_replica() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    await a.close("s1", "t1")
    assert await b.live_elsewhere("s1", "t1") is None


async def test_agent_path_gets_an_honest_error() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    ex = RPAExecutor(session_manager=b)
    ex._playwright_available = True
    res = await ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://93.184.215.14/"},
        session_id="s1",
        tenant_id="t1",
    )
    assert not res.success
    assert "another" in (res.error or "") and "replica" in (res.error or "")


# ── API: 409 on the wrong replica ────────────────────────────────────────────


def _api(manager: BrowserSessionManager) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rpa_router)
    app.state.rpa_session_manager = manager
    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=AssertionError("must not execute"))
    app.state.rpa_executor = executor
    return app


@pytest.fixture
def replicas() -> Any:
    import asyncio

    a, b, _redis = _pair()
    asyncio.run(_open_on(a, "s1", _CTX.tenant_id))
    return a, b


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/rpa/execute", {"tool_name": "rpa_open_url", "arguments": {}, "session_id": "s1"}),
        ("get", "/rpa/sessions/s1/screenshot", None),
        ("get", "/rpa/sessions/s1/current-view", None),
    ],
)
def test_session_on_another_replica_is_409(
    replicas: Any, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    _a, b = replicas
    client = TestClient(_api(b), raise_server_exceptions=False)
    kwargs: dict[str, Any] = {"headers": {"X-API-Key": _KEY}}
    if payload is not None:
        kwargs["json"] = payload
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 409, resp.text
    assert "another" in resp.json()["detail"] and "replica" in resp.json()["detail"]


def test_unknown_session_screenshot_is_still_404(replicas: Any) -> None:
    _a, b = replicas
    client = TestClient(_api(b), raise_server_exceptions=False)
    resp = client.get("/rpa/sessions/nope/screenshot", headers={"X-API-Key": _KEY})
    assert resp.status_code == 404
