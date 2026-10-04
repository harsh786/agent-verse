"""SSRF-01: the worker MCP health probe connects through the pinned client.

``check_mcp_health`` validated every hop with ``request_public`` but connected
on a plain ``httpx.AsyncClient``, which resolves the name AGAIN to connect. A
connector host whose DNS answer flips from a public IP (checked) to 127.0.0.1
(connected) made the worker probe internal services. The probe now uses
``public_async_client``: the socket is dialled only to the address that was
checked at connect time.
"""

from __future__ import annotations

from typing import Any

import httpcore
import pytest

import app.net.ssrf_guard as g
from app.mcp.registry import MCPServerConfig


def _rebinding_resolver() -> Any:
    calls: list[str] = []

    def _resolve(host: str) -> list[str]:
        calls.append(host)
        # First answer (the up-front check) is public; every later one is loopback.
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    return _resolve


def _record_dials(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    dialled: list[str] = []

    async def _connect_tcp(self: Any, host: str, port: int, **kw: Any) -> Any:
        dialled.append(host)
        raise httpcore.ConnectError("dial recorded")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", _connect_tcp)
    return dialled


def test_run_probe_refuses_a_rebinding_host_at_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    from tests.scaling._mcp_health_fakes import install_registry

    cfg = MCPServerConfig(server_id="s1", name="rebind", url="http://rebind.example")
    install_registry(monkeypatch, [("t1", "s1", cfg)])
    monkeypatch.setattr(g, "_resolve_host", _rebinding_resolver())
    dialled = _record_dials(monkeypatch)

    async def _persist(snaps: list[dict[str, Any]]) -> int:
        return len(snaps)

    monkeypatch.setattr(tasks, "_persist_health_snapshots", _persist)
    out = tasks.check_mcp_health.run()
    assert dialled == []  # never dialled the rebinding name (nor 127.0.0.1)
    assert out["results"][0]["status"] == "unreachable"


def test_legacy_redis_keys_are_never_scanned(monkeypatch: pytest.MonkeyPatch) -> None:
    """a02-F034-N1: the old mcp:servers:* scan (and its 50-key fallback) is gone."""
    from app.scaling import tasks
    from tests.scaling._mcp_health_fakes import install_registry

    install_registry(monkeypatch, [])

    async def _scan(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("legacy mcp:servers:* keys scanned")
        yield  # pragma: no cover

    import redis.asyncio as aioredis

    fake = aioredis.from_url("redis://fake/0")
    monkeypatch.setattr(fake, "scan_iter", _scan)
    out = tasks.check_mcp_health.run()
    assert out["status"] == "ok" and out["servers_checked"] == 0
