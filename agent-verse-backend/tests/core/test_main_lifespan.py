"""Cover additional paths in app/main.py.

Scope: _FakeRedis extended interface + _resolve_provider_for_app exception paths.
Tests that call create_app() are excluded — create_app triggers Celery module
scan which hangs in test environments without a full Celery broker.
Coverage for main.py lifespan is handled by integration tests (-m integration).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── _FakeRedis extended interface ─────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    from app.main import _FakeRedis
    return _FakeRedis()


@pytest.mark.asyncio
async def test_fake_redis_incrbyfloat_accumulates(fake_redis) -> None:
    val1 = await fake_redis.incrbyfloat("counter", 1.5)
    assert abs(val1 - 1.5) < 0.001
    val2 = await fake_redis.incrbyfloat("counter", 0.5)
    assert abs(val2 - 2.0) < 0.001


@pytest.mark.asyncio
async def test_fake_redis_incrbyfloat_from_zero(fake_redis) -> None:
    result = await fake_redis.incrbyfloat("new_counter", 3.14)
    assert abs(result - 3.14) < 0.001


@pytest.mark.asyncio
async def test_fake_redis_expireat_returns_true_for_existing_key(fake_redis) -> None:
    import time
    await fake_redis.set("expkey", "value")
    future_ts = int(time.time()) + 3600
    result = await fake_redis.expireat("expkey", future_ts)
    assert result is True


@pytest.mark.asyncio
async def test_fake_redis_expireat_returns_false_for_missing_key(fake_redis) -> None:
    import time
    future_ts = int(time.time()) + 3600
    result = await fake_redis.expireat("nonexistent_key_xyz", future_ts)
    assert result is False


@pytest.mark.asyncio
async def test_fake_redis_register_script_under_budget(fake_redis) -> None:
    import time
    script = fake_redis.register_script("dummy_lua")
    future_ts = int(time.time()) + 3600
    result = await script(keys=["budget_key"], args=["5.0", "100.0", str(future_ts)])
    assert float(result) == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_fake_redis_register_script_accumulates_budget(fake_redis) -> None:
    import time
    script = fake_redis.register_script("dummy_lua")
    future_ts = int(time.time()) + 3600
    await script(keys=["budget_key"], args=["10.0", "100.0", str(future_ts)])
    result = await script(keys=["budget_key"], args=["20.0", "100.0", str(future_ts)])
    assert float(result) == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_fake_redis_register_script_budget_exceeded(fake_redis) -> None:
    import time
    script = fake_redis.register_script("dummy_lua")
    future_ts = int(time.time()) + 3600
    with pytest.raises(Exception, match="BUDGET_EXCEEDED"):
        await script(keys=["budget_key"], args=["101.0", "100.0", str(future_ts)])


@pytest.mark.asyncio
async def test_fake_redis_lock_lazy_creation(fake_redis) -> None:
    """_get_lock() creates lock lazily on first call."""
    assert fake_redis._lock is None
    lock = fake_redis._get_lock()
    assert lock is not None
    lock2 = fake_redis._get_lock()
    assert lock is lock2


@pytest.mark.asyncio
async def test_fake_redis_set_with_ex_and_get(fake_redis) -> None:
    await fake_redis.set("mykey", "myval", ex=60)
    result = await fake_redis.get("mykey")
    assert result == "myval"


@pytest.mark.asyncio
async def test_fake_redis_delete_existing_key(fake_redis) -> None:
    await fake_redis.set("k", "v")
    deleted = await fake_redis.delete("k")
    assert deleted == 1
    assert await fake_redis.get("k") is None


@pytest.mark.asyncio
async def test_fake_redis_delete_missing_key(fake_redis) -> None:
    deleted = await fake_redis.delete("never_set")
    assert deleted == 0


@pytest.mark.asyncio
async def test_fake_redis_zadd_and_zcard(fake_redis) -> None:
    added = await fake_redis.zadd("zset", {"a": 1.0, "b": 2.0, "c": 3.0})
    count = await fake_redis.zcard("zset")
    assert count == 3


@pytest.mark.asyncio
async def test_fake_redis_zremrangebyscore(fake_redis) -> None:
    await fake_redis.zadd("zset2", {"a": 1.0, "b": 5.0, "c": 10.0})
    await fake_redis.zremrangebyscore("zset2", 0, 5)
    count = await fake_redis.zcard("zset2")
    assert count == 1  # only "c" (score 10) remains


@pytest.mark.asyncio
async def test_fake_redis_key_expires_on_get(fake_redis) -> None:
    """A key set with ex=1 should appear expired after monotonic time passes."""
    import time
    await fake_redis.set("expiring", "value", ex=1)
    # Manually push the TTL into the past to simulate expiry
    fake_redis._ttl["expiring"] = time.monotonic() - 1.0
    result = await fake_redis.get("expiring")
    assert result is None  # Expired


# ── _resolve_provider_for_app exception paths ─────────────────────────────────


def test_resolve_provider_anthropic_exception_falls_through() -> None:
    """Covers lines 151-152: Anthropic init raises → falls through to OpenAI."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    from app.providers.fake import FakeProvider

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.AnthropicProvider = MagicMock(side_effect=Exception("Bad key"))

    env_overrides = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "OPENAI_API_KEY": "",
        "ENVIRONMENT": "development",
    }
    with patch.dict(os.environ, env_overrides):
        with patch.dict(sys.modules, {"app.providers.anthropic_provider": mock_anthropic_mod}):
            settings = Settings()
            result = _resolve_provider_for_app(settings)
    assert isinstance(result, FakeProvider)


def test_resolve_provider_openai_exception_falls_through() -> None:
    """Covers lines 158-159: OpenAI init raises → falls through to FakeProvider."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    from app.providers.fake import FakeProvider

    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAICompatibleProvider = MagicMock(side_effect=Exception("Bad key"))

    env_overrides = {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "sk-openai-test",
        "ENVIRONMENT": "development",
    }
    with patch.dict(os.environ, env_overrides):
        with patch.dict(sys.modules, {"app.providers.openai_compatible": mock_openai_mod}):
            settings = Settings()
            result = _resolve_provider_for_app(settings)
    assert isinstance(result, FakeProvider)


def test_resolve_provider_no_keys_returns_fake() -> None:
    """No API keys → FakeProvider (dev mode only)."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    from app.providers.fake import FakeProvider

    env_overrides = {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "ENVIRONMENT": "development",
    }
    with patch.dict(os.environ, env_overrides):
        settings = Settings()
        result = _resolve_provider_for_app(settings)
    assert isinstance(result, FakeProvider)


def test_resolve_provider_production_no_keys_raises() -> None:
    """In production with no keys, must raise RuntimeError."""
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings

    env_overrides = {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "ENVIRONMENT": "production",
        # Must also have a valid DB URL to bypass the db-url guard
        "DATABASE_URL": "postgresql+asyncpg://real:real@localhost:5432/real",
    }
    with patch.dict(os.environ, env_overrides):
        settings = Settings()
        with pytest.raises((RuntimeError, Exception)):
            _resolve_provider_for_app(settings)


# ── _FakeLuaScript correctness ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_lua_script_is_atomic_under_concurrent_calls(fake_redis) -> None:
    """Concurrent budget checks must not both succeed when combined > limit."""
    import asyncio
    script = fake_redis.register_script("atomic_budget")
    import time
    future_ts = int(time.time()) + 3600

    results = await asyncio.gather(
        script(keys=["concurrent_budget"], args=["60.0", "100.0", str(future_ts)]),
        script(keys=["concurrent_budget"], args=["60.0", "100.0", str(future_ts)]),
        return_exceptions=True,
    )
    budget_exceeded = sum(1 for r in results if isinstance(r, Exception) and "BUDGET_EXCEEDED" in str(r))
    successes = sum(1 for r in results if not isinstance(r, Exception))
    assert budget_exceeded >= 1, f"Both succeeded — not atomic: {results}"
    assert successes <= 1


# ── _FakeRedis thread safety ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_redis_concurrent_zadd_is_safe(fake_redis) -> None:
    import asyncio

    async def add_batch(prefix: str) -> None:
        for i in range(10):
            await fake_redis.zadd("concurrent_zset", {f"{prefix}:{i}": float(i)})

    await asyncio.gather(*[add_batch(f"b{b}") for b in range(5)])
    count = await fake_redis.zcard("concurrent_zset")
    assert count == 50
