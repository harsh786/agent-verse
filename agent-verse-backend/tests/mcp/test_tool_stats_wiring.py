"""MCPCLI-01: tool_capabilities stats are recorded in every durable process.

``MCPClient._db`` used to be read with ``getattr(self, "_db", None)`` and was
never assigned anywhere, so ``_update_tool_stats`` returned immediately and
success_rate / latency never moved. It now defaults to the durable registry's
session factory (API lifespan and every worker build the registry with one).
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis

from app.mcp.client import MCPClient, ToolCallResult
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def test_db_defaults_to_the_durable_registry_factory() -> None:
    factory = object()
    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    registry.db_factory = factory
    assert MCPClient(registry)._db is factory


def test_explicit_db_overrides_and_redis_only_registry_has_none() -> None:
    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    client = MCPClient(registry)
    assert client._db is None
    override = object()
    client._db = override
    assert client._db is override


async def test_call_tool_records_stats_with_the_registry_factory() -> None:
    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(name="svc", url="https://svc.example.com"), tenant_ctx=CTX
    )
    factory = object()
    registry.db_factory = factory
    client = MCPClient(registry)
    seen: list[dict[str, Any]] = []

    async def _impl(*_a: Any, **_k: Any) -> ToolCallResult:
        return ToolCallResult(tool_name="search", success=True, output={}, server_id=server_id)

    async def _stats(*args: Any, **kwargs: Any) -> None:
        seen.append({"args": args, **kwargs})

    async def _usage(*_a: Any) -> None:
        return None

    client._call_tool_impl = _impl  # type: ignore[method-assign]
    client._update_tool_stats = _stats  # type: ignore[method-assign]
    client._record_goal_usage = _usage  # type: ignore[method-assign]
    result = await client.call_tool(
        server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX
    )
    assert result.success
    assert seen and seen[0]["db"] is factory and seen[0]["success"] is True
