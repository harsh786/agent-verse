"""MCPCLI-04/05/06: circuit-breaker accounting is honest.

* MCPCLI-04 — an error while checking the breaker was swallowed (``except
  Exception: pass``) and the call went ahead unguarded; a breaker that could not
  be built returned ``None`` (no breaker at all). Now a check error refuses the
  call with an honest failure, and a breaker that cannot be built on Redis is a
  process-local one instead of none.
* MCPCLI-05 — with the circuit open, a stale cached result was returned as
  ``success=True`` indistinguishable from a live call. It is now marked stale.
* MCPCLI-06 — every non-raising dispatch recorded a breaker *success*, including
  ``ToolCallResult(success=False)`` returned by the OpenAPI / builtin / Jira paths,
  so a connector that always fails never tripped its breaker.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient, ToolCallResult
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.reliability.circuit_breaker import CircuitBreaker
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


async def _client_with_server() -> tuple[MCPClient, str]:
    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    server_id = await registry.register(
        MCPServerConfig(name="svc", url="https://svc.example.com/mcp"), tenant_ctx=CTX
    )
    client = MCPClient(registry)

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    client._record_goal_usage = _noop  # type: ignore[method-assign]
    client._update_tool_stats = _noop  # type: ignore[method-assign]
    return client, server_id


async def test_breaker_check_error_refuses_the_call() -> None:
    client, server_id = await _client_with_server()
    cb = AsyncMock()
    cb.can_call_async = AsyncMock(side_effect=RuntimeError("cb bookkeeping bug"))
    client._get_circuit_breaker = lambda *a, **k: cb  # type: ignore[method-assign]
    impl = AsyncMock()
    client._call_tool_impl = impl  # type: ignore[method-assign]

    result = await client.call_tool(
        server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX
    )
    assert result.success is False
    assert "circuit breaker" in result.error.lower()
    impl.assert_not_called()


def test_breaker_that_cannot_be_built_on_redis_is_local_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("bad redis client")

    monkeypatch.setattr(
        "app.reliability.redis_circuit_breaker.RedisCircuitBreaker", _Broken
    )
    client = MCPClient(MCPRegistry(fakeredis.aioredis.FakeRedis()), redis=object())
    cb = client._get_circuit_breaker("srv", tenant_id="t1")
    assert isinstance(cb, CircuitBreaker)


async def test_open_circuit_stale_cache_is_marked_stale() -> None:
    client, server_id = await _client_with_server()
    cb = AsyncMock()
    cb.can_call_async = AsyncMock(return_value=False)
    client._get_circuit_breaker = lambda *a, **k: cb  # type: ignore[method-assign]
    cache = AsyncMock()
    cache.get_stale = AsyncMock(return_value={"rows": [1]})
    client._tool_cache = cache

    result = await client.call_tool(
        server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX
    )
    assert result.success is True
    assert result.stale is True
    assert result.output == {"rows": [1]}


async def test_failed_result_records_a_breaker_failure() -> None:
    client, server_id = await _client_with_server()
    cb = AsyncMock()
    cb.can_call_async = AsyncMock(return_value=True)
    client._get_circuit_breaker = lambda *a, **k: cb  # type: ignore[method-assign]

    async def _impl(*_a: Any, **_k: Any) -> ToolCallResult:
        return ToolCallResult(
            tool_name="search", success=False, error="HTTP 503: upstream down",
            server_id=server_id,
        )

    client._call_tool_impl = _impl  # type: ignore[method-assign]
    result = await client.call_tool(
        server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX
    )
    assert result.success is False
    cb.record_failure_async.assert_awaited_once()
    cb.record_success_async.assert_not_called()


async def test_argument_error_is_not_the_servers_fault() -> None:
    """A validation error caused by the caller's arguments must not trip the breaker."""
    client, server_id = await _client_with_server()
    cb = AsyncMock()
    cb.can_call_async = AsyncMock(return_value=True)
    client._get_circuit_breaker = lambda *a, **k: cb  # type: ignore[method-assign]

    async def _impl(*_a: Any, **_k: Any) -> ToolCallResult:
        return ToolCallResult(
            tool_name="search", success=False,
            error="JSON-RPC error -32602: Invalid params: missing required argument 'q'",
            server_id=server_id,
        )

    client._call_tool_impl = _impl  # type: ignore[method-assign]
    await client.call_tool(server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX)
    cb.record_failure_async.assert_not_called()


async def test_success_records_success() -> None:
    client, server_id = await _client_with_server()
    cb = AsyncMock()
    cb.can_call_async = AsyncMock(return_value=True)
    client._get_circuit_breaker = lambda *a, **k: cb  # type: ignore[method-assign]

    async def _impl(*_a: Any, **_k: Any) -> ToolCallResult:
        return ToolCallResult(tool_name="search", success=True, output={}, server_id=server_id)

    client._call_tool_impl = _impl  # type: ignore[method-assign]
    await client.call_tool(server_id=server_id, tool_name="search", arguments={}, tenant_ctx=CTX)
    cb.record_success_async.assert_awaited_once()
    cb.record_failure_async.assert_not_called()
