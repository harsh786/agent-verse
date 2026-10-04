"""Shared fakes for check_mcp_health: a keyset-paged registry and a fake Redis.

check_mcp_health reads connectors from Postgres ``mcp_servers`` through
``app.mcp.health_sweep.fetch_connector_page`` (a02-F034-N1); these tests swap
that page source for an in-memory table and Redis for fakeredis.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp.registry import MCPServerConfig


def install_registry(
    monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str, MCPServerConfig | dict[str, Any]]]
) -> list[tuple[Any, int]]:
    table = sorted(
        (
            (t, s, c.model_dump(mode="json") if isinstance(c, MCPServerConfig) else c)
            for t, s, c in rows
        ),
        key=lambda r: (r[0], r[1]),
    )
    calls: list[tuple[Any, int]] = []

    async def _fetch(_factory: Any, after: Any, limit: int) -> list[tuple[str, str, Any]]:
        calls.append((after, limit))
        return [r for r in table if after is None or (r[0], r[1]) > tuple(after)][:limit]

    monkeypatch.setattr("app.mcp.health_sweep.fetch_connector_page", _fetch)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    def _from_url(*_a: Any, **_k: Any) -> Any:
        return redis

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    monkeypatch.setenv("REDIS_URL", "redis://fake/0")
    return calls
