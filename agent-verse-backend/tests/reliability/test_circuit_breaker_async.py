"""Test that in-memory CircuitBreaker has proper async interface matching RedisCircuitBreaker."""
from __future__ import annotations

import inspect
import time

import pytest

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_can_call_async_closed_circuit() -> None:
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    assert await cb.can_call_async() is True


@pytest.mark.asyncio
async def test_can_call_async_opens_after_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    await cb.record_failure_async()
    await cb.record_failure_async()
    assert await cb.can_call_async() is False
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_record_success_async_resets_circuit() -> None:
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    await cb.record_failure_async()
    await cb.record_failure_async()
    assert cb.state == CircuitState.OPEN
    # Simulate cooldown by backdating _opened_at
    cb._opened_at = time.monotonic() - 61
    assert await cb.can_call_async() is True  # transitions to HALF_OPEN
    await cb.record_success_async()
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_async_interface_matches_redis_circuit_breaker() -> None:
    """Ensure in-memory CB exposes same async interface as RedisCircuitBreaker."""
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

    rcb_async_methods = {m for m in dir(RedisCircuitBreaker) if "async" in m}
    for method in rcb_async_methods:
        assert hasattr(CircuitBreaker, method), f"CircuitBreaker missing {method}"
        assert inspect.iscoroutinefunction(
            getattr(CircuitBreaker, method)
        ), f"{method} must be async"


@pytest.mark.asyncio
async def test_mcp_client_does_not_swallow_cb_errors() -> None:
    """When in-memory CircuitBreaker is used (no Redis), tool calls work without AttributeError."""
    from unittest.mock import AsyncMock, MagicMock

    from app.mcp.client import MCPClient
    from app.reliability.circuit_breaker import CircuitBreaker

    registry = MagicMock()
    registry.get = AsyncMock(return_value=None)
    registry.list_all = AsyncMock(return_value=[])

    client = MCPClient(registry=registry)
    # Force in-memory circuit breaker (as if Redis was unavailable)
    client._circuit_breakers["test-server:"] = CircuitBreaker()

    ctx = MagicMock()
    ctx.tenant_id = "test"
    # Should NOT raise AttributeError — the async methods now exist
    result = await client.call_tool(
        server_id="test-server",
        tool_name="nonexistent_tool",
        arguments={},
        tenant_ctx=ctx,
    )
    # Fails because server not found, not AttributeError
    assert result.success is False


@pytest.mark.asyncio
async def test_half_open_probe_succeeds_resets_to_closed() -> None:
    """HALF_OPEN → success → CLOSED transition via async methods."""
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    await cb.record_failure_async()
    assert cb.state == CircuitState.OPEN
    # cooldown=0 so immediately allows probe
    assert await cb.can_call_async() is True
    assert cb.state == CircuitState.HALF_OPEN
    await cb.record_success_async()
    assert cb.state == CircuitState.CLOSED
    assert await cb.can_call_async() is True


@pytest.mark.asyncio
async def test_half_open_probe_fails_reopens() -> None:
    """HALF_OPEN → failure → OPEN (re-opens circuit)."""
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
    await cb.record_failure_async()
    assert cb.state == CircuitState.OPEN
    # Allow probe (cooldown=0)
    assert await cb.can_call_async() is True
    assert cb.state == CircuitState.HALF_OPEN
    # Probe fails → re-opens
    await cb.record_failure_async()
    assert cb.state == CircuitState.OPEN
