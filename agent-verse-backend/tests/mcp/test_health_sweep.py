"""a02-F034-N1: the connector health sweep probes the durable registry.

check_mcp_health scanned only the legacy ``mcp:servers:*`` Redis keys, so every
connector created since MCPREG-01 (Postgres ``mcp_servers``) was never probed.
These unit tests drive ``run_health_sweep`` with a fake page source; the
Postgres keyset query itself is covered in test_health_sweep_integration.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp import health_sweep
from app.mcp.health_sweep import CURSOR_KEY, LOCK_KEY, run_health_sweep


def _rows(n: int, tenant: str = "t1") -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (tenant, f"s{i:04d}", {"name": f"c{i}", "url": f"https://c{i}.example.com"})
        for i in range(n)
    ]


class _Table:
    """Keyset pages over a sorted in-memory table; records every page request."""

    def __init__(self, rows: list[tuple[str, str, dict[str, Any]]]) -> None:
        self.rows = sorted(rows, key=lambda r: (r[0], r[1]))
        self.calls: list[tuple[tuple[str, str] | None, int]] = []

    async def fetch(
        self, _factory: Any, after: tuple[str, str] | None, limit: int
    ) -> list[tuple[str, str, dict[str, Any]]]:
        self.calls.append((after, limit))
        rows = [r for r in self.rows if after is None or (r[0], r[1]) > after]
        return rows[:limit]


class _Persist:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    async def __call__(self, snaps: list[dict[str, Any]]) -> int:
        self.snapshots.extend(snaps)
        return len(snaps)


async def _ok_probe(_cfg: Any) -> dict[str, Any]:
    return {"status": "healthy", "latency_ms": 1, "error": None}


async def test_every_connector_is_probed_in_bounded_pages() -> None:
    table = _Table(_rows(7) + _rows(3, tenant="t2"))
    persist = _Persist()
    out = await run_health_sweep(
        factory=None, redis=fakeredis.aioredis.FakeRedis(), persist=persist,
        probe=_ok_probe, fetch=table.fetch, page_size=4,
    )
    assert out["servers_checked"] == 10 and out["completed_pass"] is True
    assert {(s["tenant_id"], s["server_id"]) for s in persist.snapshots} == {
        (r[0], r[1]) for r in table.rows
    }
    assert all(limit == 4 for _, limit in table.calls)
    assert table.calls[0][0] is None and table.calls[1][0] == ("t1", "s0003")


async def test_cursor_resumes_across_runs_within_budget() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    table = _Table(_rows(10))
    ticks = iter(range(1000))

    def clock() -> float:  # each loop check advances one "second"
        return float(next(ticks))

    first = await run_health_sweep(
        factory=None, redis=redis, persist=_Persist(), probe=_ok_probe,
        fetch=table.fetch, page_size=3, budget_s=3.0, clock=clock,
    )
    assert first["servers_checked"] == 6 and first["completed_pass"] is False
    assert await redis.get(CURSOR_KEY) is not None

    persist = _Persist()
    second = await run_health_sweep(
        factory=None, redis=redis, persist=persist, probe=_ok_probe,
        fetch=table.fetch, page_size=3,
    )
    assert [s["server_id"] for s in persist.snapshots] == ["s0006", "s0007", "s0008", "s0009"]
    assert second["completed_pass"] is True
    assert await redis.get(CURSOR_KEY) is None  # next run starts a new pass


async def test_overlapping_run_is_skipped() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    await redis.set(LOCK_KEY, "someone-else", ex=60)
    table = _Table(_rows(2))
    out = await run_health_sweep(
        factory=None, redis=redis, persist=_Persist(), probe=_ok_probe, fetch=table.fetch,
    )
    assert out["status"] == "skipped" and table.calls == []
    assert await redis.get(LOCK_KEY) == b"someone-else"


async def test_lock_is_released_after_a_run() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    await run_health_sweep(
        factory=None, redis=redis, persist=_Persist(), probe=_ok_probe,
        fetch=_Table(_rows(1)).fetch,
    )
    assert await redis.get(LOCK_KEY) is None


async def test_probes_run_concurrently_but_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    active = peak = 0

    async def _slow(_cfg: Any) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"status": "healthy", "latency_ms": 10, "error": None}

    await run_health_sweep(
        factory=None, redis=None, persist=_Persist(), probe=_slow,
        fetch=_Table(_rows(30)).fetch, page_size=30, concurrency=5,
    )
    assert peak == 5


async def test_hung_probe_times_out_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_sweep, "PROBE_TIMEOUT_S", 0.01)

    async def _hang(_cfg: Any) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {}

    persist = _Persist()
    await run_health_sweep(
        factory=None, redis=None, persist=persist, probe=_hang,
        fetch=_Table(_rows(1)).fetch,
    )
    assert persist.snapshots[0]["status"] == "unreachable"
    assert "timed out" in persist.snapshots[0]["error"]


async def test_builtin_and_invalid_configs() -> None:
    rows = [
        ("t1", "b1", {"name": "gh", "url": "builtin://github"}),
        ("t1", "x1", {"url": 12}),  # not a valid MCPServerConfig
    ]
    persist = _Persist()
    probed: list[Any] = []

    async def _probe(cfg: Any) -> dict[str, Any]:
        probed.append(cfg)
        return {"status": "healthy", "latency_ms": 1, "error": None}

    await run_health_sweep(
        factory=None, redis=None, persist=persist, probe=_probe, fetch=_Table(rows).fetch,
    )
    assert probed == []
    assert [s["status"] for s in persist.snapshots] == ["invalid_config"]
