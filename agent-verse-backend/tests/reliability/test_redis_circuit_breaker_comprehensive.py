"""Comprehensive tests for app/reliability/redis_circuit_breaker.py — targeting 90%+ coverage."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState
from app.reliability.redis_circuit_breaker import RedisCircuitBreaker


def _make_redis(
    state: str | None = None,
    opened_at: str | None = None,
    failures: int = 0,
) -> AsyncMock:
    mock = AsyncMock()
    mock.get = AsyncMock(side_effect=lambda key: (
        state.encode() if state is not None and "state" in key
        else opened_at.encode() if opened_at is not None and "opened_at" in key
        else None
    ))
    mock.incr = AsyncMock(return_value=failures + 1)
    mock.set = AsyncMock()
    mock.delete = AsyncMock()
    mock.expire = AsyncMock()
    return mock


def _make_breaker(
    redis=None, tenant_id: str = "t1", tool_name: str = "tool_a",
    failure_threshold: int = 3, cooldown_seconds: float = 60.0,
) -> RedisCircuitBreaker:
    if redis is None:
        redis = AsyncMock()
    return RedisCircuitBreaker(
        redis_client=redis,
        tenant_id=tenant_id,
        tool_name=tool_name,
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
    )


# ── Key generation ─────────────────────────────────────────────────────────────

class TestKeyGeneration:
    def test_prefix_format(self) -> None:
        breaker = _make_breaker(tenant_id="t1", tool_name="deploy")
        assert breaker._prefix == "cb:t1:deploy"

    def test_key_suffix(self) -> None:
        breaker = _make_breaker()
        assert breaker._key("state") == "cb:t1:tool_a:state"
        assert breaker._key("failures") == "cb:t1:tool_a:failures"
        assert breaker._key("opened_at") == "cb:t1:tool_a:opened_at"


# ── get_state ─────────────────────────────────────────────────────────────────

class TestGetState:
    async def test_closed_when_no_key(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        breaker = _make_breaker(redis=mock_redis)
        state = await breaker.get_state()
        assert state == CircuitState.CLOSED

    async def test_open_state_from_redis(self) -> None:
        mock_redis = AsyncMock()
        # Redis with decode_responses=True returns strings; simulate that
        mock_redis.get = AsyncMock(return_value="open")
        breaker = _make_breaker(redis=mock_redis)
        state = await breaker.get_state()
        assert state == CircuitState.OPEN

    async def test_half_open_state_from_redis(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="half_open")
        breaker = _make_breaker(redis=mock_redis)
        state = await breaker.get_state()
        assert state == CircuitState.HALF_OPEN

    async def test_redis_error_falls_back_to_fallback_state(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))
        breaker = _make_breaker(redis=mock_redis)
        state = await breaker.get_state()
        assert state == breaker._fallback.state  # in-memory fallback


# ── can_call_async ─────────────────────────────────────────────────────────────

class TestCanCallAsync:
    async def test_closed_allows_call(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        breaker = _make_breaker(redis=mock_redis)
        assert await breaker.can_call_async() is True

    async def test_open_blocks_call(self) -> None:
        mock_redis = AsyncMock()
        call_count = 0

        async def mock_get(key: str) -> str | None:
            nonlocal call_count
            call_count += 1
            if "state" in key:
                return "open"  # string, not bytes
            if "opened_at" in key:
                # Return current time so cooldown hasn't elapsed
                return str(time.monotonic())
            return None

        mock_redis.get = mock_get
        mock_redis.set = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, cooldown_seconds=60.0)
        result = await breaker.can_call_async()
        assert result is False

    async def test_open_transitions_to_half_open_after_cooldown(self) -> None:
        mock_redis = AsyncMock()
        # opened_at is far in the past so cooldown has elapsed
        past_time = str(time.monotonic() - 120.0)

        async def mock_get(key: str) -> str | None:
            if "state" in key:
                return "open"  # string, not bytes
            if "opened_at" in key:
                return past_time
            return None

        mock_redis.get = mock_get
        mock_redis.set = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, cooldown_seconds=60.0)
        result = await breaker.can_call_async()
        assert result is True
        mock_redis.set.assert_called_once_with(
            "cb:t1:tool_a:state", CircuitState.HALF_OPEN.value
        )

    async def test_open_no_opened_at_blocks(self) -> None:
        mock_redis = AsyncMock()

        async def mock_get(key: str) -> str | None:
            if "state" in key:
                return "open"  # string, not bytes
            return None  # no opened_at

        mock_redis.get = mock_get
        breaker = _make_breaker(redis=mock_redis)
        result = await breaker.can_call_async()
        assert result is False

    async def test_half_open_allows_probe(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"half_open")
        breaker = _make_breaker(redis=mock_redis)
        result = await breaker.can_call_async()
        assert result is True

    async def test_redis_error_falls_back_to_in_memory(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))
        breaker = _make_breaker(redis=mock_redis)
        # In-memory fallback is CLOSED → can call
        result = await breaker.can_call_async()
        assert result == breaker._fallback.can_call()


# ── record_failure_async ───────────────────────────────────────────────────────

class TestRecordFailureAsync:
    async def test_increments_failure_count(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.set = AsyncMock()
        mock_redis.expire = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, failure_threshold=3)
        await breaker.record_failure_async()
        mock_redis.incr.assert_called_once()

    async def test_opens_circuit_at_threshold(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=3)  # at threshold
        mock_redis.set = AsyncMock()
        mock_redis.expire = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, failure_threshold=3)
        await breaker.record_failure_async()
        # Should set state to OPEN
        set_calls = [str(c) for c in mock_redis.set.call_args_list]
        assert any(CircuitState.OPEN.value in c for c in set_calls)

    async def test_below_threshold_stays_closed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)  # below threshold
        mock_redis.set = AsyncMock()
        mock_redis.expire = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, failure_threshold=3)
        await breaker.record_failure_async()
        # set should not be called with OPEN
        set_calls = [str(c) for c in mock_redis.set.call_args_list]
        assert not any(CircuitState.OPEN.value in c for c in set_calls)

    async def test_ttl_applied(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.set = AsyncMock()
        mock_redis.expire = AsyncMock()
        breaker = _make_breaker(redis=mock_redis, cooldown_seconds=60.0)
        await breaker.record_failure_async()
        # expire called for state and failures keys
        assert mock_redis.expire.call_count >= 1

    async def test_redis_error_uses_fallback(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(side_effect=Exception("Redis down"))
        breaker = _make_breaker(redis=mock_redis)
        # Should not raise — uses in-memory fallback
        await breaker.record_failure_async()
        assert breaker._fallback._failure_count == 1


# ── record_success_async ───────────────────────────────────────────────────────

class TestRecordSuccessAsync:
    async def test_deletes_all_keys(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        breaker = _make_breaker(redis=mock_redis)
        await breaker.record_success_async()
        mock_redis.delete.assert_called_once()
        deleted_keys = set(mock_redis.delete.call_args[0])
        assert "cb:t1:tool_a:state" in deleted_keys
        assert "cb:t1:tool_a:failures" in deleted_keys
        assert "cb:t1:tool_a:opened_at" in deleted_keys

    async def test_redis_error_uses_fallback(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        breaker = _make_breaker(redis=mock_redis)
        breaker._fallback._state = CircuitState.OPEN
        await breaker.record_success_async()
        assert breaker._fallback.state == CircuitState.CLOSED


# ── Sync fallback interface ────────────────────────────────────────────────────

class TestSyncFallbackInterface:
    def test_is_closed_delegates_to_fallback(self) -> None:
        breaker = _make_breaker()
        assert breaker.is_closed() == breaker._fallback.is_closed()

    def test_can_call_delegates_to_fallback(self) -> None:
        breaker = _make_breaker()
        assert breaker.can_call() == breaker._fallback.can_call()

    def test_record_failure_delegates_to_fallback(self) -> None:
        breaker = _make_breaker()
        breaker.record_failure()
        assert breaker._fallback._failure_count == 1

    def test_record_success_delegates_to_fallback(self) -> None:
        breaker = _make_breaker()
        breaker._fallback._failure_count = 5
        breaker._fallback._state = CircuitState.OPEN
        breaker.record_success()
        assert breaker._fallback.state == CircuitState.CLOSED

    def test_state_property_reads_fallback(self) -> None:
        breaker = _make_breaker()
        assert breaker.state == breaker._fallback.state


# ── CLOSED→OPEN→HALF_OPEN transitions ──────────────────────────────────────────

class TestStateTransitions:
    async def test_closed_to_open_via_failures(self) -> None:
        """Simulate CLOSED→OPEN by recording failures to threshold."""
        failure_count = 0

        async def mock_incr(key: str) -> int:
            nonlocal failure_count
            failure_count += 1
            return failure_count

        mock_redis = AsyncMock()
        mock_redis.incr = mock_incr
        mock_redis.set = AsyncMock()
        mock_redis.expire = AsyncMock()

        breaker = _make_breaker(redis=mock_redis, failure_threshold=3)
        for _ in range(3):
            await breaker.record_failure_async()

        # Verify that OPEN state was set at threshold
        set_calls = [c[0] for c in mock_redis.set.call_args_list]
        assert any(CircuitState.OPEN.value in str(c) for c in set_calls)

    async def test_open_to_half_open_after_cooldown(self) -> None:
        """OPEN circuit transitions to HALF_OPEN after cooldown."""
        past_opened_at = str(time.monotonic() - 120.0)

        async def mock_get(key: str) -> str | None:
            if "state" in key:
                return "open"  # string, not bytes
            if "opened_at" in key:
                return past_opened_at
            return None

        mock_redis = AsyncMock()
        mock_redis.get = mock_get
        mock_redis.set = AsyncMock()

        breaker = _make_breaker(redis=mock_redis, cooldown_seconds=60.0)
        can_call = await breaker.can_call_async()
        assert can_call is True  # promoted to HALF_OPEN

    async def test_half_open_success_resets_to_closed(self) -> None:
        """After a HALF_OPEN probe succeeds, all keys are deleted (CLOSED)."""
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        breaker = _make_breaker(redis=mock_redis)
        await breaker.record_success_async()
        mock_redis.delete.assert_called_once()
