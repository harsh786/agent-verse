"""Capability registry endpoints (a02-F032-01/02/05/06/07).

* F032-06: ``discover_all_tools`` wrapped the whole per-connector loop in one
  try, so the first failing connector aborted the rest and the partial list
  was returned as complete. Each connector is now discovered on its own.
* F032-02: ``/capabilities/search`` and ``/missing`` swallowed discovery errors
  (a failure read as "no tools"). Failures are 503 or listed per connector.
* F032-01: ``/missing`` matched catalog names as substrings and compared them
  with tool names; ``can_proceed`` was true whenever any tool existed.
* F032-05: ``GET /capabilities`` had no LIMIT.
* F032-07: live discovery of every connector ran serially per request with no
  rate limit.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import connectors as connectors_mod
from app.mcp.client import DiscoveryReport, MCPClient, ToolDefinition, strict_discovery
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy import ip_rate_limit
from tests.api.test_connectors_extra2 import _CTX, _VALID_KEY, _make_app, _make_registry

H = {"X-API-Key": _VALID_KEY}


@pytest.fixture(autouse=True)
def _fresh_rate_windows() -> None:
    ip_rate_limit._local_windows.clear()


# ── F032-06: per-connector discovery ─────────────────────────────────────────


async def _registry_with(*names: str) -> tuple[MCPRegistry, list[str]]:
    reg = _make_registry()
    ids = [
        await reg.register(
            MCPServerConfig(name=n, url=f"https://{n}.example.com"), tenant_ctx=_CTX
        )
        for n in names
    ]
    return reg, ids


@pytest.mark.asyncio
async def test_one_failing_connector_does_not_abort_the_others() -> None:
    reg, ids = await _registry_with("a", "b", "c")
    client = MCPClient(registry=reg)

    async def discover(*, server_id: str, tenant_ctx: Any) -> list[ToolDefinition]:
        if server_id == ids[0]:
            raise ValueError("Connector URL blocked by SSRF guard")
        return [ToolDefinition(name=f"t_{server_id[:4]}", description="", server_id=server_id)]

    client.discover_tools = discover  # type: ignore[method-assign]
    report = await client.discover_all_tools_report(tenant_ctx=_CTX)
    assert {t.server_id for t in report.tools} == {ids[1], ids[2]}
    assert report.complete is False
    assert report.errors == [
        {"connector_id": ids[0], "error": "Connector URL blocked by SSRF guard"}
    ]
    # The best-effort list keeps the connectors that answered.
    assert len(await client.discover_all_tools(tenant_ctx=_CTX)) == 2


@pytest.mark.asyncio
async def test_connectors_are_discovered_concurrently_with_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg, ids = await _registry_with("a", "b", "slow")
    client = MCPClient(registry=reg)
    both_started = asyncio.Event()
    started: set[str] = set()

    async def discover(*, server_id: str, tenant_ctx: Any) -> list[ToolDefinition]:
        if server_id == ids[2]:
            await asyncio.sleep(10)
        started.add(server_id)
        if {ids[0], ids[1]} <= started:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)  # needs concurrency
        return [ToolDefinition(name="t", description="", server_id=server_id)]

    client.discover_tools = discover  # type: ignore[method-assign]
    report = await client.discover_all_tools_report(tenant_ctx=_CTX, timeout_s=0.5)
    assert len(report.tools) == 2
    assert report.errors == [
        {"connector_id": ids[2], "error": "discovery timed out after 0.5s"}
    ]


@pytest.mark.asyncio
async def test_strict_discovery_raises_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.mcp.client.assert_public_url", lambda *_a, **_k: None)

    async def _ok(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("app.mcp.client._assert_public_url_async", _ok)
    reg, (sid,) = await _registry_with("down")
    client = MCPClient(registry=reg)

    def _failing_client() -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    client._http_client = _failing_client  # type: ignore[method-assign]
    assert await client.discover_tools(server_id=sid, tenant_ctx=_CTX) == []  # lenient
    with strict_discovery(), pytest.raises(httpx.ConnectError):
        await client.discover_tools(server_id=sid, tenant_ctx=_CTX)
    report = await client.discover_all_tools_report(tenant_ctx=_CTX)
    assert report.errors and "connection refused" in report.errors[0]["error"]


# ── F032-02: errors are reported, never "no tools" ───────────────────────────


def test_search_reports_connector_errors() -> None:
    report = DiscoveryReport(
        tools=[ToolDefinition(name="search_issues", description="Search issues")],
        errors=[{"connector_id": "c-2", "error": "timed out"}],
    )
    mcp = MagicMock()
    mcp.discover_all_tools_report = AsyncMock(return_value=report)
    resp = TestClient(_make_app(mcp_client=mcp)).get(
        "/connectors/capabilities/search?q=issues", headers=H
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["complete"] is False
    assert body["connector_errors"] == [{"connector_id": "c-2", "error": "timed out"}]
    assert body["results"][0]["tool_name"] == "search_issues"


def test_connector_tools_route_is_502_when_discovery_fails() -> None:
    from app.mcp import client as client_mod

    async def discover(*, server_id: str, tenant_ctx: Any) -> list:
        if client_mod._STRICT_DISCOVERY.get():
            raise httpx.ConnectError("refused")
        return []

    reg = _make_registry()
    sid = asyncio.run(
        reg.register(MCPServerConfig(name="x", url="https://x.example.com/mcp"), tenant_ctx=_CTX)
    )
    mcp = MagicMock()
    mcp.discover_tools = discover
    tc = TestClient(_make_app(registry=reg, mcp_client=mcp), raise_server_exceptions=False)
    resp = tc.get(f"/connectors/{sid}/tools", headers=H)
    assert resp.status_code == 502


# ── F032-01: what the goal names, against what the tenant registered ─────────


def _missing(goal: str, registry: MCPRegistry | None = None, errors: Any = ()) -> dict:
    mcp = MagicMock()
    mcp.discover_all_tools_report = AsyncMock(
        return_value=DiscoveryReport(tools=[], errors=list(errors))
    )
    resp = TestClient(_make_app(registry=registry, mcp_client=mcp)).get(
        "/connectors/capabilities/missing", params={"goal": goal}, headers=H
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_named_unregistered_connector_is_missing_and_blocks() -> None:
    body = _missing("Open a pull request on GitHub")
    assert [m["connector"] for m in body["missing_connectors"]] == ["github"]
    assert body["can_proceed"] is False


@pytest.mark.asyncio
async def test_a_registered_connector_is_not_missing() -> None:
    reg = _make_registry()
    await reg.register(
        MCPServerConfig(name="github", url="https://api.github.com/mcp"), tenant_ctx=_CTX
    )
    body = _missing("Open a pull request on GitHub", registry=reg)
    assert body["missing_connectors"] == []
    assert body["can_proceed"] is True


def test_catalog_names_match_whole_words_only() -> None:
    # 'steam' is a catalog connector; 'steamship' must not name it.
    body = _missing("Update the steamship manifest and the ramparts plan")
    assert body["missing_connectors"] == []
    assert body["can_proceed"] is True


def test_incomplete_discovery_cannot_proceed() -> None:
    body = _missing("Summarise the week", errors=[{"connector_id": "c", "error": "down"}])
    assert body["can_proceed"] is False
    assert body["complete"] is False


# ── F032-07: rate limit ───────────────────────────────────────────────────────


def test_capability_search_is_rate_limited_per_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connectors_mod, "CAPABILITY_SEARCH_RATE_LIMIT", 2)
    mcp = MagicMock()
    mcp.discover_all_tools_report = AsyncMock(return_value=DiscoveryReport())
    tc = TestClient(_make_app(mcp_client=mcp))
    codes = [
        tc.get("/connectors/capabilities/search?q=x", headers=H).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    assert mcp.discover_all_tools_report.await_count == 2  # the refused one ran nothing


# ── F032-05: GET /capabilities is paginated ──────────────────────────────────


class _Rows:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows


class _PagingDb:
    """Applies the statement's LIMIT/OFFSET to a fixed table (records the SQL)."""

    def __init__(self, n: int) -> None:
        self.rows = [(f"tool_{i:02d}", "c1", "d", "low", "ok", 1.0, 1.0) for i in range(n)]
        self.sql: list[str] = []

    def __call__(self) -> _PagingDb:
        return self

    async def __aenter__(self) -> _PagingDb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        self.sql.append(sql)
        if "tool_capabilities" not in sql:
            return _Rows([])
        p = params or {}
        return _Rows(self.rows[p["off"] : p["off"] + p["lim"]])


def test_capabilities_list_is_paged() -> None:
    db = _PagingDb(5)
    app = _make_app(db_factory=db)
    tc = TestClient(app)
    first = tc.get("/connectors/capabilities?limit=2", headers=H)
    assert [r["tool_name"] for r in first.json()] == ["tool_00", "tool_01"]
    assert first.headers["X-Next-Offset"] == "2"
    last = tc.get("/connectors/capabilities?limit=2&offset=4", headers=H)
    assert [r["tool_name"] for r in last.json()] == ["tool_04"]
    assert "X-Next-Offset" not in last.headers
    capability_sql = [s for s in db.sql if "tool_capabilities" in s][-1]
    assert "ORDER BY tool_name, connector_id LIMIT :lim OFFSET :off" in capability_sql
    assert "tenant_id = :tid" in capability_sql
    assert tc.get("/connectors/capabilities?limit=5000", headers=H).status_code == 422
