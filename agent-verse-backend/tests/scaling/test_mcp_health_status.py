"""check_mcp_health: real status classification, snapshots persisted, no 50-key cap.

Regressions: any HTTP response (including 5xx) was reported ``status: ok``;
results were never written to ``connector_health_snapshots`` (so the health
history API was always empty); and the scan stopped after 50 keys across all
tenants.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.mcp.registry import MCPServerConfig


def _install(monkeypatch: pytest.MonkeyPatch, n: int, code_for: Any) -> list[dict[str, Any]]:
    from app.scaling import tasks

    cfgs = {
        f"mcp:servers:tenant-{i % 3}:srv-{i}": MCPServerConfig(
            server_id=f"srv-{i}", name=f"c{i}", url=f"https://93.184.216.{i % 250 + 1}"
        )
        for i in range(n)
    }

    class _R:
        async def scan_iter(self, **kw: Any) -> Any:
            for k in cfgs:
                yield k

        async def get(self, key: str) -> str:
            return cfgs[key].model_dump_json()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _R())

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(code_for(str(req.url)))

    # The probe connects through the pinned client (SSRF-01); swap its transport.
    monkeypatch.setattr(
        "app.net.ssrf_guard.public_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(_handler), **kw),
    )
    written: list[dict[str, Any]] = []

    async def _persist(snaps: list[dict[str, Any]]) -> int:
        written.extend(snaps)
        return len(snaps)

    monkeypatch.setattr(tasks, "_persist_health_snapshots", _persist)
    return written


def test_5xx_is_unhealthy_not_ok_and_snapshots_are_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    codes = {0: 200, 1: 503, 2: 401}
    written = _install(monkeypatch, 3, lambda url: codes[int(url.split(".")[-1].split("/")[0]) - 1])
    out = tasks.check_mcp_health.run()
    statuses = {r["server"]: r["status"] for r in out["results"]}
    assert statuses == {"c0": "healthy", "c1": "unhealthy", "c2": "degraded"}
    assert {(s["tenant_id"], s["server_id"], s["status"]) for s in written} == {
        ("tenant-0", "srv-0", "healthy"),
        ("tenant-1", "srv-1", "unhealthy"),
        ("tenant-2", "srv-2", "degraded"),
    }


def test_scan_is_not_capped_at_50(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    written = _install(monkeypatch, 120, lambda url: 200)
    out = tasks.check_mcp_health.run()
    assert out["servers_checked"] == 120
    assert len(written) == 120


@pytest.mark.parametrize(("code", "expected"), [(204, "healthy"), (404, "degraded"), (500, "unhealthy")])
def test_classify_health(code: int, expected: str) -> None:
    from app.scaling.tasks import _classify_health

    assert _classify_health(code) == expected
