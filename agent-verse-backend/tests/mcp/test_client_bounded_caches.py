"""a02-F030-11: the MCP client's breaker and schema caches are bounded and expire.

They were plain dicts keyed by tenant x server with no eviction, and a cached
tool schema was never invalidated when its connector changed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.mcp import client as client_mod
from app.mcp.bounded_cache import BoundedTTLCache
from app.mcp.client import MCPClient, ToolCallResult, ToolDefinition
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str = "t-bounded") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_cache_evicts_least_recently_used_beyond_maxsize() -> None:
    cache: BoundedTTLCache[str, int] = BoundedTTLCache(maxsize=2, ttl_s=60)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # a is now the most recently used
    cache["c"] = 3
    assert "b" not in cache
    assert cache.get("a") == 1 and cache.get("c") == 3
    assert len(cache) == 2


def test_cache_expires_after_ttl_and_sliding_refreshes_on_use() -> None:
    clock = _Clock()
    fixed: BoundedTTLCache[str, int] = BoundedTTLCache(maxsize=10, ttl_s=10, clock=clock)
    sliding: BoundedTTLCache[str, int] = BoundedTTLCache(
        maxsize=10, ttl_s=10, sliding=True, clock=clock
    )
    fixed["k"] = 1
    sliding["k"] = 1
    clock.now += 8
    assert fixed.get("k") == 1 and sliding.get("k") == 1
    clock.now += 8  # 16 s after the write, 8 s after the last use
    assert fixed.get("k") is None
    assert sliding.get("k") == 1
    with pytest.raises(KeyError):
        fixed["k"]


def test_circuit_breakers_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_BREAKER_CACHE_MAX", 3)
    client = MCPClient(registry=MCPRegistry(redis=None))
    breakers = [client._get_circuit_breaker(f"srv-{i}", tenant_id="t") for i in range(10)]
    assert len(client._circuit_breakers) == 3
    # The same key returns the same breaker while it is cached.
    assert client._get_circuit_breaker("srv-9", tenant_id="t") is breakers[-1]


def test_schema_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_SCHEMA_CACHE_MAX", 2)
    client = MCPClient(registry=MCPRegistry(redis=None))
    for i in range(5):
        client._schema_cache[f"k{i}"] = []
    assert len(client._schema_cache) == 2


@pytest.mark.asyncio
async def test_schema_cache_is_invalidated_when_the_connector_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "assert_public_url", lambda *_a, **_kw: None)
    registry = MCPRegistry(redis=None)
    client = MCPClient(registry=registry, timeout=5.0)
    before = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    after = before.model_copy(update={"url": "http://api2.example.com"})
    discovered: list[str] = []

    async def fake_discover(*, server_id: str, tenant_ctx: TenantContext) -> list:
        discovered.append(server_id)
        return [ToolDefinition(name="search", description="", input_schema={"type": "object"})]

    ok = ToolCallResult(tool_name="search", success=True, output="ok")
    get = AsyncMock(side_effect=[before, before, after])
    with (
        patch.object(registry, "get", get),
        patch.object(client, "discover_tools", side_effect=fake_discover),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=ok)),
    ):
        for _ in range(3):
            await client.call_tool(
                server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
            )
    # Cached for the unchanged config, rediscovered once the connector changed.
    assert discovered == ["srv-1", "srv-1"]
