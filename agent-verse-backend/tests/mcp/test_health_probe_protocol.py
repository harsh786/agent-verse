"""a02-F034-01 / a02-F034-02: what the connector health sweep probes.

F034-01: the probe was always ``GET {base}/health``; a standard MCP server has
no such route, answered 404 and was recorded ``degraded``. MCP endpoints now
get the protocol's ``initialize``; REST connectors fall back to ``GET {base}``
when there is no health route.

F034-02: built-in (``builtin://``) connectors were skipped and never had a
snapshot. They now get an in-process readiness snapshot.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.mcp import health_sweep
from app.mcp.health_sweep import builtin_readiness, probe_connector, run_health_sweep
from app.mcp.registry import MCPServerConfig


def _transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        "app.net.ssrf_guard.public_async_client",
        lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(_record),
            **{k: v for k, v in kw.items() if k != "allowed_networks"},
        ),
    )

    async def _public(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("app.net.ssrf_guard.assert_public_url_async", _public)
    return seen


def _mcp_server(request: httpx.Request) -> httpx.Response:
    """A standard Streamable-HTTP MCP server: no /health route."""
    if request.url.path != "/mcp":
        return httpx.Response(404)
    if request.method == "DELETE":
        return httpx.Response(204)
    body = json.loads(request.content)
    assert body["method"] == "initialize"
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26"}},
        headers={"Mcp-Session-Id": "sess-1"},
    )


async def test_a_standard_mcp_server_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _transport(monkeypatch, _mcp_server)
    out = await probe_connector(MCPServerConfig(name="m", url="https://mcp.example.com/mcp"))
    assert out["status"] == "healthy", out
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/mcp"), ("DELETE", "/mcp")]
    assert seen[1].headers["Mcp-Session-Id"] == "sess-1"  # the probe's session is closed


async def test_an_mcp_jsonrpc_error_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _transport(
        monkeypatch,
        lambda r: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32600, "message": "bad"}}
        ),
    )
    out = await probe_connector(MCPServerConfig(name="m", url="https://mcp.example.com/mcp"))
    assert out["status"] == "degraded"
    assert "MCP error" in out["error"]


async def test_rest_connector_without_health_route_falls_back_to_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _transport(
        monkeypatch,
        lambda r: httpx.Response(404 if r.url.path == "/v1/health" else 200),
    )
    out = await probe_connector(MCPServerConfig(name="r", url="https://api.example.com/v1"))
    assert out["status"] == "healthy"
    assert [r.url.path for r in seen] == ["/v1/health", "/v1"]


async def test_auth_refusal_says_the_server_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _transport(monkeypatch, lambda r: httpx.Response(401))
    out = await probe_connector(MCPServerConfig(name="m", url="https://mcp.example.com/mcp"))
    assert out["status"] == "degraded"
    assert "server is up" in out["error"]


# ── F034-02: built-ins ────────────────────────────────────────────────────────


def test_builtin_readiness_states(monkeypatch: pytest.MonkeyPatch) -> None:
    no_creds = MCPServerConfig(name="GitHub", url="builtin://", builtin_type="builtin-github")
    assert builtin_readiness(no_creds)["status"] == "degraded"
    with_creds = no_creds.model_copy(update={"auth_config": {"token": "vault:ref-1"}})
    assert builtin_readiness(with_creds) == {"status": "ready", "latency_ms": 0, "error": None}
    unknown = MCPServerConfig(name="x", url="builtin://", builtin_type="builtin-nope")
    assert builtin_readiness(unknown)["status"] == "unhealthy"

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mcp_connector_mongodb_enabled", False)
    mongo = MCPServerConfig(
        name="orders-db",
        url="builtin://",
        builtin_type="builtin-mongodb",
        auth_config={"url": "vault:ref-2"},
    )
    assert builtin_readiness(mongo)["status"] == "disabled"


async def test_builtins_get_a_snapshot_without_an_http_probe() -> None:
    rows = [
        ("t1", "builtin-github:main", {"name": "GitHub", "url": "builtin://",
                                       "builtin_type": "builtin-github",
                                       "auth_config": {"token": "vault:ref"}}),
    ]
    probed: list[Any] = []
    snaps: list[dict[str, Any]] = []

    async def _probe(cfg: Any) -> dict[str, Any]:
        probed.append(cfg)
        return {"status": "healthy", "latency_ms": 1, "error": None}

    async def _persist(batch: list[dict[str, Any]]) -> int:
        snaps.extend(batch)
        return len(batch)

    async def _fetch(_f: Any, after: Any, limit: int) -> list:
        return [] if after else rows

    await run_health_sweep(
        factory=None, redis=None, persist=_persist, probe=_probe, fetch=_fetch
    )
    assert probed == []
    assert [(s["server_id"], s["status"]) for s in snaps] == [("builtin-github:main", "ready")]
    assert health_sweep.builtin_readiness is builtin_readiness
