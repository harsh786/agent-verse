"""Tests for the Agent Lab API (app/api/lab.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.lab import lab_run, list_lab_tools, router as lab_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-lab", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_labkey"
AUTH_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(simulation_runner: object | None = None, mcp_client: object | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(lab_router)
    app.state.simulation_runner = simulation_runner
    app.state.mcp_client = mcp_client
    return app


# ---------------------------------------------------------------------------
# POST /lab/run
# ---------------------------------------------------------------------------

def test_lab_run_no_api_key_401() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "test"})
    assert resp.status_code == 401


def test_lab_run_simulation_no_runner_503() -> None:
    client = TestClient(_make_app(simulation_runner=None), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "test"}, headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_lab_run_simulation_uses_the_real_runner() -> None:
    """Regression: /lab/run called SimulationRunner.run(), which does not exist
    (the mocks hid it), so every lab simulation was a 500. Use the real runner."""
    from app.enterprise.simulation import SimulationRunner

    client = TestClient(
        _make_app(simulation_runner=SimulationRunner()), raise_server_exceptions=False
    )
    resp = client.post(
        "/lab/run",
        json={"goal": "search the flights", "mock_tools": {"flights.search": "3 results"}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "simulation"
    assert body["run_id"]
    assert body["used_real_llm"] is False
    assert body["result"]["mock_tools_used"] == ["flights.search"]


def test_lab_run_simulation_passes_tenant_and_app_state() -> None:
    run = MagicMock(run_id="r1", status="completed", used_real_llm=False, result={"x": 1})
    runner = MagicMock()
    runner.start = AsyncMock(return_value=run)
    app = _make_app(simulation_runner=runner)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g"}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    kwargs = runner.start.await_args.kwargs
    assert kwargs["goal"] == "g" and kwargs["mock_tools"] == {}
    assert kwargs["tenant_ctx"].tenant_id == _CTX.tenant_id
    assert kwargs["app_state"] is app.state


def test_lab_run_simulation_runner_raises_returns_500_without_leaking() -> None:
    runner = MagicMock()
    runner.start = AsyncMock(side_effect=ValueError("secret internals"))
    client = TestClient(_make_app(simulation_runner=runner), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g"}, headers=AUTH_HEADERS)
    assert resp.status_code == 500
    assert "secret internals" not in resp.text


@pytest.mark.parametrize("mode", ["comparison", "live"])
def test_lab_run_unimplemented_modes_are_501(mode: str) -> None:
    """comparison returned a note and live a fake 'queued' while doing nothing."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g", "mode": mode}, headers=AUTH_HEADERS)
    assert resp.status_code == 501


def test_lab_run_unknown_mode_is_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g", "mode": "bogus"}, headers=AUTH_HEADERS)
    assert resp.status_code == 422


def test_lab_run_default_mode_is_simulation() -> None:
    client = TestClient(_make_app(simulation_runner=None), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g"}, headers=AUTH_HEADERS)
    # default mode="simulation" with no runner configured -> 503
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_lab_run_handler_no_tenant_raises_401() -> None:
    from app.api.lab import LabRunRequest

    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await lab_run(LabRunRequest(goal="g"), req)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# GET /lab/tools
# ---------------------------------------------------------------------------

def test_list_lab_tools_no_api_key_401() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/lab/tools")
    assert resp.status_code == 401


def test_list_lab_tools_no_mcp_client() -> None:
    client = TestClient(_make_app(mcp_client=None), raise_server_exceptions=False)
    resp = client.get("/lab/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"tools": [], "total": 0}


def test_list_lab_tools_with_mcp_client_lists_discovered_tools() -> None:
    """Regression: discovery was a stub that always returned []."""
    from app.mcp.client import ToolDefinition

    mcp = MagicMock()
    mcp.discover_all_tools = AsyncMock(
        return_value=[ToolDefinition(name="gh.search", description="d", server_id="s1")]
    )
    client = TestClient(_make_app(mcp_client=mcp), raise_server_exceptions=False)
    resp = client.get("/lab/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {
        "tools": [{"name": "gh.search", "description": "d", "server_id": "s1"}],
        "total": 1,
    }


def test_list_lab_tools_discovery_failure_is_503() -> None:
    mcp = MagicMock()
    mcp.discover_all_tools = AsyncMock(side_effect=RuntimeError("down"))
    client = TestClient(_make_app(mcp_client=mcp), raise_server_exceptions=False)
    assert client.get("/lab/tools", headers=AUTH_HEADERS).status_code == 503


@pytest.mark.asyncio
async def test_list_lab_tools_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await list_lab_tools(req)
    assert exc_info.value.status_code == 401
