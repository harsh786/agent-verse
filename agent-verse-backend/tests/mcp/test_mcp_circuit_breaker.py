"""Tests for MCP client circuit breaker wiring."""
from __future__ import annotations

import builtins
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx

from app.mcp.client import CircuitBreakerOpenError, MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="cb-test", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ─── Minimal FakeRedis ────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self._d[k] = v

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._d:
                del self._d[k]
                count += 1
        return count

    async def incr(self, k: str) -> int:
        self._d[k] = str(int(self._d.get(k, "0")) + 1)
        return int(self._d[k])

    async def expire(self, k: str, ttl: int) -> None:
        pass

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    return MCPRegistry(redis=FakeRedis())


@pytest.fixture
def client(registry):
    return MCPClient(registry=registry)


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_get_circuit_breaker_returns_none_without_redis(client):
    """_get_circuit_breaker returns None when _redis is not set."""
    assert client._redis is None
    cb = client._get_circuit_breaker("server1")
    assert cb is None


def test_get_circuit_breaker_returns_instance_with_redis(client):
    """_get_circuit_breaker returns a RedisCircuitBreaker when _redis is set."""
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

    client._redis = FakeRedis()
    cb = client._get_circuit_breaker("server1")

    assert cb is not None
    assert isinstance(cb, RedisCircuitBreaker)


def test_circuit_breaker_reused_for_same_server_id(client):
    """The same circuit breaker instance is returned for the same server_id."""
    client._redis = FakeRedis()

    cb1 = client._get_circuit_breaker("server1")
    cb2 = client._get_circuit_breaker("server1")

    assert cb1 is cb2


def test_different_server_ids_get_different_circuit_breakers(client):
    """Different server IDs produce distinct circuit breaker instances."""
    client._redis = FakeRedis()

    cb_a = client._get_circuit_breaker("server-A")
    cb_b = client._get_circuit_breaker("server-B")

    assert cb_a is not cb_b


@pytest.mark.asyncio
@respx.mock
async def test_call_tool_succeeds_when_cb_allows(registry):
    """call_tool returns ToolCallResult(success=True) when circuit is closed."""
    cfg = MCPServerConfig(
        name="test-server",
        url="http://mcp.example.com",
        auth_type="bearer",
        auth_config={"token": "tok"},
    )
    server_id = await registry.register(cfg, tenant_ctx=_CTX)

    respx.post(f"http://mcp.example.com/tools/test_tool").mock(
        return_value=httpx.Response(200, json={"result": "ok"})
    )

    mcp = MCPClient(registry=registry)
    # Wire a Redis mock with CB always open → closed (can_call returns True)
    mcp._redis = FakeRedis()
    # Manually pre-create CB so we can verify it doesn't block
    cb = mcp._get_circuit_breaker(server_id)
    # The CB state is CLOSED by default, so can_call_async should return True

    result = await mcp.call_tool(
        server_id=server_id,
        tool_name="test_tool",
        arguments={},
        tenant_ctx=_CTX,
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_call_tool_raises_circuit_breaker_open_error(registry):
    """call_tool raises CircuitBreakerOpenError when the circuit breaker is open."""
    cfg = MCPServerConfig(
        name="flaky-server",
        url="http://flaky.example.com",
        auth_type="bearer",
        auth_config={"token": "tok"},
    )
    server_id = await registry.register(cfg, tenant_ctx=_CTX)

    mcp = MCPClient(registry=registry)
    # Create a mock CB that always reports circuit open
    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=False)
    mcp._redis = FakeRedis()  # enable CB path
    mcp._circuit_breakers[server_id] = mock_cb

    with pytest.raises(CircuitBreakerOpenError):
        await mcp.call_tool(
            server_id=server_id,
            tool_name="some_tool",
            arguments={},
            tenant_ctx=_CTX,
        )
