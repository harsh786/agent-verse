"""Tests for LLMConfigStore and RedisCircuitBreaker (in-memory Redis stubs)."""
from __future__ import annotations

import pytest

from app.services.llm_config_store import LLMConfigStore


# ── Minimal in-memory Redis fake (no external deps) ───────────────────────────

class _FakeRedis:
    """Async dict-backed Redis stub — supports get/set/delete/incr/expire."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._d.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._d[key] = value

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._d:
                del self._d[k]
                removed += 1
        return removed

    async def incr(self, key: str) -> int:
        val = int(self._d.get(key, "0")) + 1
        self._d[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._d


class _BrokenRedis:
    """Redis stub that raises on every operation — simulates connection failure."""

    async def get(self, key: str) -> str | None:
        raise ConnectionError("Redis down")

    async def set(self, key: str, value: str, **_: object) -> None:
        raise ConnectionError("Redis down")

    async def delete(self, *keys: str) -> int:
        raise ConnectionError("Redis down")

    async def incr(self, key: str) -> int:
        raise ConnectionError("Redis down")

    async def expire(self, key: str, seconds: int) -> bool:
        raise ConnectionError("Redis down")


# ── LLMConfigStore ─────────────────────────────────────────────────────────────

async def test_set_and_get_config() -> None:
    store = LLMConfigStore(redis_client=_FakeRedis())
    await store.set_config("t1", "anthropic", "enc_key_abc", "claude-opus-4-8")
    cfg = await store.get_config("t1")
    assert cfg is not None
    assert cfg["provider"] == "anthropic"
    assert cfg["encrypted_key"] == "enc_key_abc"
    assert cfg["model"] == "claude-opus-4-8"


async def test_get_config_returns_none_when_not_set() -> None:
    store = LLMConfigStore(redis_client=_FakeRedis())
    cfg = await store.get_config("nonexistent")
    assert cfg is None


async def test_delete_config_removes_entry() -> None:
    store = LLMConfigStore(redis_client=_FakeRedis())
    await store.set_config("t1", "openai", "enc_key_xyz", "gpt-4o")
    await store.delete_config("t1")
    cfg = await store.get_config("t1")
    assert cfg is None


async def test_base_url_stored_and_retrieved() -> None:
    store = LLMConfigStore(redis_client=_FakeRedis())
    await store.set_config(
        "t2", "ollama", "enc_key_local", "llama3", base_url="http://localhost:11434"
    )
    cfg = await store.get_config("t2")
    assert cfg is not None
    assert cfg["base_url"] == "http://localhost:11434"


async def test_base_url_none_when_not_provided() -> None:
    store = LLMConfigStore(redis_client=_FakeRedis())
    await store.set_config("t3", "anthropic", "key", "claude-haiku-3-5")
    cfg = await store.get_config("t3")
    assert cfg is not None
    assert cfg["base_url"] is None


async def test_two_tenants_are_isolated() -> None:
    redis = _FakeRedis()
    store = LLMConfigStore(redis_client=redis)
    await store.set_config("ta", "anthropic", "key_a", "claude-opus-4-8")
    await store.set_config("tb", "openai", "key_b", "gpt-4o")
    cfg_a = await store.get_config("ta")
    cfg_b = await store.get_config("tb")
    assert cfg_a is not None and cfg_a["provider"] == "anthropic"
    assert cfg_b is not None and cfg_b["provider"] == "openai"


async def test_redis_get_failure_returns_none_gracefully() -> None:
    """A Redis failure on get should log a warning and return None, not raise."""
    store = LLMConfigStore(redis_client=_BrokenRedis())
    cfg = await store.get_config("t1")
    assert cfg is None


async def test_redis_set_failure_is_swallowed_gracefully() -> None:
    """A Redis failure on set should log a warning and not raise."""
    store = LLMConfigStore(redis_client=_BrokenRedis())
    # Should not raise
    await store.set_config("t1", "anthropic", "key", "claude-opus-4-8")


async def test_redis_delete_failure_is_swallowed_gracefully() -> None:
    store = LLMConfigStore(redis_client=_BrokenRedis())
    await store.delete_config("t1")  # Should not raise


# ── RedisCircuitBreaker ────────────────────────────────────────────────────────

async def test_redis_circuit_breaker_starts_closed() -> None:
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
    from app.reliability.circuit_breaker import CircuitState

    breaker = RedisCircuitBreaker(
        redis_client=_FakeRedis(), tenant_id="t1", tool_name="github", failure_threshold=3
    )
    assert await breaker.get_state() == CircuitState.CLOSED
    assert await breaker.can_call_async() is True


async def test_redis_circuit_breaker_opens_after_threshold() -> None:
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
    from app.reliability.circuit_breaker import CircuitState

    breaker = RedisCircuitBreaker(
        redis_client=_FakeRedis(), tenant_id="t1", tool_name="github", failure_threshold=3
    )
    await breaker.record_failure_async()
    await breaker.record_failure_async()
    # 2 failures — still closed
    assert await breaker.get_state() == CircuitState.CLOSED
    await breaker.record_failure_async()
    # 3rd failure — circuit opens
    assert await breaker.get_state() == CircuitState.OPEN
    assert await breaker.can_call_async() is False


async def test_redis_circuit_breaker_success_resets_to_closed() -> None:
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
    from app.reliability.circuit_breaker import CircuitState

    breaker = RedisCircuitBreaker(
        redis_client=_FakeRedis(), tenant_id="t1", tool_name="github", failure_threshold=3
    )
    await breaker.record_failure_async()
    await breaker.record_failure_async()
    await breaker.record_failure_async()
    assert await breaker.get_state() == CircuitState.OPEN

    await breaker.record_success_async()
    assert await breaker.get_state() == CircuitState.CLOSED
    assert await breaker.can_call_async() is True


async def test_redis_circuit_breaker_different_tools_are_isolated() -> None:
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
    from app.reliability.circuit_breaker import CircuitState

    redis = _FakeRedis()
    github = RedisCircuitBreaker(
        redis_client=redis, tenant_id="t1", tool_name="github", failure_threshold=2
    )
    jira = RedisCircuitBreaker(
        redis_client=redis, tenant_id="t1", tool_name="jira", failure_threshold=2
    )

    await github.record_failure_async()
    await github.record_failure_async()
    # github open, jira still closed
    assert await github.get_state() == CircuitState.OPEN
    assert await jira.get_state() == CircuitState.CLOSED


async def test_redis_circuit_breaker_redis_failure_uses_fallback() -> None:
    """When Redis is down, the in-memory fallback circuit breaker is used."""
    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

    breaker = RedisCircuitBreaker(
        redis_client=_BrokenRedis(), tenant_id="t1", tool_name="tool", failure_threshold=2
    )
    # get_state falls back to in-memory (CLOSED)
    from app.reliability.circuit_breaker import CircuitState
    assert await breaker.get_state() == CircuitState.CLOSED

    # can_call_async falls back to in-memory can_call()
    assert await breaker.can_call_async() is True

    # record_failure_async falls back to in-memory
    await breaker.record_failure_async()
    await breaker.record_failure_async()
    # Fallback should have opened the in-memory breaker
    assert breaker.state == CircuitState.OPEN
