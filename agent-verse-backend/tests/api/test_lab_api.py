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


def test_lab_run_simulation_success() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value={"steps": 3, "outcome": "success"})
    client = TestClient(_make_app(simulation_runner=runner), raise_server_exceptions=False)
    resp = client.post(
        "/lab/run",
        json={"goal": "book a flight", "agent_id": "a1", "mock_tools": {"flights": {}}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "simulation"
    assert body["result"]["outcome"] == "success"
    runner.run.assert_awaited_once_with(
        goal="book a flight", agent_id="a1", mock_tools={"flights": {}}
    )


def test_lab_run_simulation_default_mock_tools_empty_dict() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value={})
    client = TestClient(_make_app(simulation_runner=runner), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g"}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    runner.run.assert_awaited_once_with(goal="g", agent_id=None, mock_tools={})


def test_lab_run_simulation_runner_raises_returns_500() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=ValueError("bad input"))
    client = TestClient(_make_app(simulation_runner=runner), raise_server_exceptions=False)
    resp = client.post("/lab/run", json={"goal": "g"}, headers=AUTH_HEADERS)
    assert resp.status_code == 500
    assert "ValueError" in resp.json()["detail"]


def test_lab_run_comparison_default_models() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/lab/run", json={"goal": "g", "mode": "comparison"}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "comparison"
    assert body["models"] == ["claude-haiku-3-5", "gpt-3.5-turbo"]
    assert body["goal"] == "g"


def test_lab_run_comparison_explicit_models() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/lab/run",
        json={"goal": "g", "mode": "comparison", "models": ["m1", "m2"]},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["m1", "m2"]


def test_lab_run_unknown_mode_returns_queued() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/lab/run", json={"goal": "g", "mode": "live"}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"mode": "live", "goal": "g", "status": "queued"}


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


def test_list_lab_tools_with_mcp_client() -> None:
    client = TestClient(_make_app(mcp_client=MagicMock()), raise_server_exceptions=False)
    resp = client.get("/lab/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_lab_tools_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await list_lab_tools(req)
    assert exc_info.value.status_code == 401
