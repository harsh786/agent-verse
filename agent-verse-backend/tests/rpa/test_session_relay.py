"""RPA-07: a session's browser is usable from every replica, not a 409.

A live Playwright page exists only in the process that opened it. A request for
that session landing on another API replica (or a worker) used to be refused with
409. It is now relayed over Redis to the owning process, which runs the tool call
on the live page and answers on a per-request reply key. Original arguments travel
(vault:// references are resolved by the owner, so no secret crosses Redis); an
owner that does not answer is an honest 504, never a fresh blank browser.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.rpa import router as rpa_router
from app.rpa.executor import RPAExecutor
from app.rpa.session_manager import BrowserSession, BrowserSessionManager
from app.tenancy.context import PlanTier, TenantContext

_TENANT = "tid-relay"
_URL = "https://93.184.215.14/"  # public IP literal: no DNS in tests


def _page() -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Owner page")
    page.screenshot = AsyncMock(return_value=b"\xff\xd8jpeg")
    page.url = "https://owner.example/"
    return page


async def _open_on(manager: BrowserSessionManager, sid: str, tenant: str) -> MagicMock:
    page = _page()
    session = BrowserSession(session_id=sid, tenant_id=tenant)
    session._browser = MagicMock()
    session._page = page
    session.ssrf_guarded = True
    manager._create_session = AsyncMock(return_value=session)  # type: ignore[method-assign]
    await manager.get_or_create(sid, tenant)
    return page


def _executor(manager: BrowserSessionManager) -> RPAExecutor:
    ex = RPAExecutor(session_manager=manager)
    ex._playwright_available = True
    return ex


@pytest.fixture
async def replicas() -> AsyncIterator[tuple[BrowserSessionManager, BrowserSessionManager, Any]]:
    server = fakeredis.FakeServer()
    a = BrowserSessionManager(redis=fakeredis.FakeAsyncRedis(server=server))
    b = BrowserSessionManager(redis=fakeredis.FakeAsyncRedis(server=server))
    page = await _open_on(a, "s1", _TENANT)  # starts a's relay server
    yield a, b, page
    await a.close_all()
    await b.close_all()


async def test_tool_call_on_another_replica_runs_on_the_owner(replicas: Any) -> None:
    a, b, page = replicas
    owner_ex, other_ex = _executor(a), _executor(b)
    b._create_session = AsyncMock(side_effect=AssertionError("no blank browser"))  # type: ignore[method-assign]
    assert owner_ex is not None  # registers the relay handler on a

    res = await other_ex.execute(
        tool_name="rpa_open_url",
        arguments={"url": _URL},
        session_id="s1",
        tenant_id=_TENANT,
    )

    assert res.success, res.error
    assert "Owner page" in res.output
    page.goto.assert_awaited_once()
    assert b.list_active() == []


async def test_secrets_are_resolved_by_the_owner_not_sent_over_redis(replicas: Any) -> None:
    a, b, page = replicas
    owner_ex, other_ex = _executor(a), _executor(b)
    seen: list[dict[str, Any]] = []

    class _OwnerInjector:
        async def resolve_arguments(self, args: dict[str, Any]) -> dict[str, Any]:
            seen.append(dict(args))
            return {**args, "text": "s3cret"}

    class _NeverHere:
        async def resolve_arguments(self, args: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("the forwarding replica must not resolve secrets")

    owner_ex._credential_injector = _OwnerInjector()
    other_ex._credential_injector = _NeverHere()
    page.fill = AsyncMock()
    page.type = AsyncMock()

    await other_ex.execute(
        tool_name="rpa_type",
        arguments={"selector": "#pw", "text": "vault://conn/password"},
        session_id="s1",
        tenant_id=_TENANT,
    )
    assert seen == [{"selector": "#pw", "text": "vault://conn/password"}]


async def test_silent_owner_is_an_honest_relay_error(
    replicas: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, _page_ = replicas
    # The owner is alive (heartbeat key) but its relay server is stuck.
    task = a._relay_task
    assert task is not None
    task.cancel()
    monkeypatch.setattr("app.rpa.executor._RELAY_EXECUTE_TIMEOUT_S", 1.0)
    b._create_session = AsyncMock(side_effect=AssertionError("no blank browser"))  # type: ignore[method-assign]

    res = await _executor(b).execute(
        tool_name="rpa_open_url", arguments={"url": _URL}, session_id="s1", tenant_id=_TENANT
    )
    assert not res.success
    assert res.error_code == "session_relay_failed"
    assert a.replica_id in (res.error or "")


async def test_owner_never_serves_another_tenants_request(replicas: Any) -> None:
    a, _b, page = replicas
    reply = await a._relay_dispatch({"op": "view", "session_id": "s1", "tenant_id": "evil"})
    assert reply["ok"] is False and reply["error_code"] == "session_not_found"
    page.screenshot.assert_not_called()


async def test_closed_session_is_not_reopened_by_a_relayed_call(replicas: Any) -> None:
    a, _b, _page_ = replicas
    _executor(a)
    await a.close("s1", _TENANT)
    a._create_session = AsyncMock(side_effect=AssertionError("must not reopen"))  # type: ignore[method-assign]
    reply = await a._relay_dispatch(
        {
            "op": "execute",
            "session_id": "s1",
            "tenant_id": _TENANT,
            "tool_name": "rpa_open_url",
            "arguments": {"url": _URL},
        }
    )
    assert reply["error_code"] == "session_not_found"


# ── API ───────────────────────────────────────────────────────────────────────


def _api(manager: BrowserSessionManager, executor: RPAExecutor) -> FastAPI:
    ctx = TenantContext(tenant_id=_TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(rpa_router)
    app.state.rpa_session_manager = manager
    app.state.rpa_executor = executor
    return app


async def test_api_on_another_replica_relays_execute_and_views(replicas: Any) -> None:
    a, b, _page_ = replicas
    _executor(a)
    app_b = _api(b, _executor(b))
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://b") as client:
        resp = await client.post(
            "/rpa/execute",
            json={"tool_name": "rpa_open_url", "arguments": {"url": _URL}, "session_id": "s1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        for path in ("/rpa/sessions/s1/screenshot", "/rpa/sessions/s1/current-view"):
            view = await client.get(path)
            assert view.status_code == 200, view.text
            body = view.json()
            assert body["url"] == "https://owner.example/"
            assert body["screenshot_data_uri"].endswith(base64.b64encode(b"\xff\xd8jpeg").decode())
        assert (await client.get("/rpa/sessions/nope/screenshot")).status_code == 404


async def test_api_silent_owner_is_504(replicas: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, _page_ = replicas
    assert a._relay_task is not None
    a._relay_task.cancel()
    monkeypatch.setattr("app.rpa.executor._RELAY_EXECUTE_TIMEOUT_S", 1.0)
    monkeypatch.setattr("app.api.rpa._RELAY_VIEW_TIMEOUT_S", 1.0)
    app_b = _api(b, _executor(b))
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://b") as client:
        resp = await client.post(
            "/rpa/execute",
            json={"tool_name": "rpa_open_url", "arguments": {"url": _URL}, "session_id": "s1"},
        )
        assert resp.status_code == 504, resp.text
        assert (await client.get("/rpa/sessions/s1/screenshot")).status_code == 504


async def test_relay_server_stops_with_the_last_session() -> None:
    server = fakeredis.FakeServer()
    a = BrowserSessionManager(redis=fakeredis.FakeAsyncRedis(server=server))
    await _open_on(a, "s1", _TENANT)
    task = a._relay_task
    assert task is not None and not task.done()
    await a.close("s1", _TENANT)
    await asyncio.wait_for(task, timeout=5)
    await a.close_all()
