"""Coverage gaps for app/main.py — _FakeRedis sorted-set ops + _FakeLuaScript 2-key format.

Missing lines targeted:
  341  — zadd body
  422-444 — _FakeLuaScript 2-key (goal+daily) format
  447  — _FakeLuaScript 1-key budget-exceeded path
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI

# ── import _FakeRedis and _FakeLuaScript from main.py ─────────────────────────

@pytest.fixture
def fake_redis():
    from app.main import _FakeRedis
    return _FakeRedis()


# ── zadd / zremrangebyscore / zcard ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_zadd_adds_new_members(fake_redis):
    added = await fake_redis.zadd("zset:1", {"member_a": 1.0, "member_b": 2.0})
    assert added == 2


@pytest.mark.asyncio
async def test_zadd_returns_zero_for_existing_members(fake_redis):
    await fake_redis.zadd("zset:1", {"m": 1.0})
    added = await fake_redis.zadd("zset:1", {"m": 2.0})  # m already exists
    assert added == 0


@pytest.mark.asyncio
async def test_zcard_returns_count(fake_redis):
    await fake_redis.zadd("zset:2", {"a": 1.0, "b": 2.0, "c": 3.0})
    count = await fake_redis.zcard("zset:2")
    assert count == 3


@pytest.mark.asyncio
async def test_zcard_returns_zero_for_missing_key(fake_redis):
    count = await fake_redis.zcard("no_such_key")
    assert count == 0


@pytest.mark.asyncio
async def test_zcard_returns_zero_for_expired_key(fake_redis):
    await fake_redis.zadd("zset:expired", {"m": 1.0})
    # Force expiry to a past time
    fake_redis._ttl["zset:expired"] = time.monotonic() - 1
    count = await fake_redis.zcard("zset:expired")
    assert count == 0


@pytest.mark.asyncio
async def test_zremrangebyscore_removes_in_range(fake_redis):
    await fake_redis.zadd("zset:3", {"low": 0.5, "mid": 5.0, "high": 9.0})
    removed = await fake_redis.zremrangebyscore("zset:3", 0.0, 6.0)
    assert removed == 2  # low + mid
    assert await fake_redis.zcard("zset:3") == 1


@pytest.mark.asyncio
async def test_zremrangebyscore_missing_key_returns_zero(fake_redis):
    removed = await fake_redis.zremrangebyscore("no_key", 0.0, 100.0)
    assert removed == 0


@pytest.mark.asyncio
async def test_zremrangebyscore_expired_key_returns_zero(fake_redis):
    await fake_redis.zadd("zset:exp", {"m": 1.0})
    fake_redis._ttl["zset:exp"] = time.monotonic() - 1
    removed = await fake_redis.zremrangebyscore("zset:exp", 0.0, 10.0)
    assert removed == 0


# ── expire / expireat / incrbyfloat ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_expire_sets_ttl(fake_redis):
    await fake_redis.set("k", "v")
    result = await fake_redis.expire("k", 60)
    assert result is True
    assert "k" in fake_redis._ttl


@pytest.mark.asyncio
async def test_expire_returns_false_for_missing_key(fake_redis):
    result = await fake_redis.expire("nonexistent", 60)
    assert result is False


@pytest.mark.asyncio
async def test_expireat_sets_absolute_ttl(fake_redis):
    await fake_redis.set("k2", "v2")
    future_ts = int(time.time()) + 3600
    result = await fake_redis.expireat("k2", future_ts)
    assert result is True
    assert "k2" in fake_redis._ttl


@pytest.mark.asyncio
async def test_incrbyfloat_increments(fake_redis):
    val1 = await fake_redis.incrbyfloat("counter", 1.5)
    assert val1 == pytest.approx(1.5)
    val2 = await fake_redis.incrbyfloat("counter", 2.5)
    assert val2 == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_incrbyfloat_starts_at_zero(fake_redis):
    val = await fake_redis.incrbyfloat("new_counter", 0.1)
    assert val == pytest.approx(0.1)


# ── register_script ──────────────────────────────────────────────────────────

def test_register_script_returns_lua_script(fake_redis):
    from app.main import _FakeLuaScript
    script = fake_redis.register_script("return 1")
    assert isinstance(script, _FakeLuaScript)


# ── _FakeLuaScript: 2-key format (goal + daily budget) ───────────────────────

@pytest.mark.asyncio
async def test_lua_2key_success(fake_redis):
    """2-key script: cost fits within both limits."""
    script = fake_redis.register_script("-- goal+daily script")
    future_ts = int(time.time()) + 3600
    result = await script(
        keys=["goal:g1", "daily:t1"],
        args=["0.5", "10.0", "100.0", str(future_ts), str(future_ts)],
    )
    assert result == "0.5:0.5"
    assert float(fake_redis._d["goal:g1"]) == pytest.approx(0.5)
    assert float(fake_redis._d["daily:t1"]) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_lua_2key_accumulates(fake_redis):
    """2-key script: second call accumulates on existing totals."""
    script = fake_redis.register_script("-- goal+daily script")
    future_ts = int(time.time()) + 3600
    await script(
        keys=["goal:g2", "daily:t2"],
        args=["1.0", "100.0", "100.0", str(future_ts), str(future_ts)],
    )
    result = await script(
        keys=["goal:g2", "daily:t2"],
        args=["2.0", "100.0", "100.0", str(future_ts), str(future_ts)],
    )
    assert result == "3.0:3.0"


@pytest.mark.asyncio
async def test_lua_2key_goal_limit_exceeded(fake_redis):
    """2-key script: raises GOAL_BUDGET_EXCEEDED when goal limit hit."""
    script = fake_redis.register_script("-- goal+daily script")
    future_ts = int(time.time()) + 3600
    with pytest.raises(Exception, match="GOAL_BUDGET_EXCEEDED"):
        await script(
            keys=["goal:g3", "daily:t3"],
            args=["5.0", "4.0", "100.0", str(future_ts), str(future_ts)],
        )


@pytest.mark.asyncio
async def test_lua_2key_daily_limit_exceeded(fake_redis):
    """2-key script: raises DAILY_BUDGET_EXCEEDED when daily limit hit."""
    script = fake_redis.register_script("-- goal+daily script")
    future_ts = int(time.time()) + 3600
    with pytest.raises(Exception, match="DAILY_BUDGET_EXCEEDED"):
        await script(
            keys=["goal:g4", "daily:t4"],
            args=["5.0", "100.0", "4.0", str(future_ts), str(future_ts)],
        )


@pytest.mark.asyncio
async def test_lua_2key_zero_limits_allow_any_cost(fake_redis):
    """2-key script: limit=0 means unlimited (no enforcement)."""
    script = fake_redis.register_script("-- goal+daily script")
    future_ts = int(time.time()) + 3600
    result = await script(
        keys=["goal:g5", "daily:t5"],
        args=["999.0", "0.0", "0.0", str(future_ts), str(future_ts)],
    )
    assert "999.0" in result


# ── _FakeLuaScript: 1-key format (legacy daily budget) ───────────────────────

@pytest.mark.asyncio
async def test_lua_1key_budget_exceeded(fake_redis):
    """1-key legacy script: raises BUDGET_EXCEEDED when over limit."""
    script = fake_redis.register_script("-- legacy daily script")
    future_ts = int(time.time()) + 3600
    with pytest.raises(Exception, match="BUDGET_EXCEEDED"):
        await script(
            keys=["daily:legacy"],
            args=["10.0", "5.0", str(future_ts)],
        )


@pytest.mark.asyncio
async def test_lua_1key_success(fake_redis):
    """1-key legacy script: increments and returns new value."""
    script = fake_redis.register_script("-- legacy daily script")
    future_ts = int(time.time()) + 3600
    result = await script(
        keys=["daily:ok"],
        args=["3.0", "100.0", str(future_ts)],
    )
    assert result == "3.0"


# ── Error handlers (lines 404-417) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_platform_error_handler_returns_json():
    """The PlatformError exception handler returns a JSONResponse with the error dict."""
    from app.core.errors import PlatformError, Severity
    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    # Find the handler and invoke it directly with a mock PlatformError
    exc = PlatformError(
        message="Something went wrong",
        code="test-error",
        severity=Severity.HIGH,
    )

    # Get the registered handler from the app's exception_handlers
    handler = app.exception_handlers.get(PlatformError)
    assert handler is not None

    from starlette.requests import Request
    mock_request = Request({"type": "http", "headers": []})
    response = await handler(mock_request, exc)

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["error"]["code"] == "test-error"


@pytest.mark.asyncio
async def test_platform_error_handler_critical_severity_logs():
    """Critical severity platform errors are logged at error level."""
    from app.core.errors import PlatformError, Severity
    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    exc = PlatformError(
        code="critical-outage",
        message="System outage",
        severity=Severity.CRITICAL,
    )

    handler = app.exception_handlers.get(PlatformError)
    with patch("app.main.logger") as mock_logger:
        from starlette.requests import Request
        mock_request = Request({"type": "http", "headers": []})
        await handler(mock_request, exc)
    mock_logger.error.assert_called()


@pytest.mark.asyncio
async def test_unhandled_error_handler_wraps_in_internal_error():
    """The unhandled Exception handler wraps the cause in InternalError and returns 500."""
    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    original_exc = RuntimeError("database connection lost")
    handler = app.exception_handlers.get(Exception)
    assert handler is not None

    from starlette.requests import Request
    mock_request = Request({"type": "http", "headers": []})
    response = await handler(mock_request, original_exc)

    assert response.status_code == 500
    body = json.loads(response.body)
    # Body is nested under "error" key; should NOT leak the original message
    error_obj = body.get("error", body)
    assert "database connection lost" not in error_obj.get("message", "")
    assert error_obj.get("code", "").startswith("INTERNAL") or "internal" in error_obj.get("code", "").lower()


@pytest.mark.asyncio
async def test_unhandled_error_handler_logs_real_cause():
    """The handler logs the real exception server-side (exc_info)."""
    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    handler = app.exception_handlers.get(Exception)
    with patch("app.main.logger") as mock_logger:
        from starlette.requests import Request
        mock_request = Request({"type": "http", "headers": []})
        await handler(mock_request, ValueError("secret value"))

    mock_logger.error.assert_called()
    # The log call should include exc_info
    call_kwargs = mock_logger.error.call_args.kwargs
    assert "exc_info" in call_kwargs or len(mock_logger.error.call_args.args) > 1


def test_register_error_handlers_registers_both_handlers():
    """_register_error_handlers registers handlers for PlatformError and Exception."""
    from app.core.errors import PlatformError
    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    assert PlatformError in app.exception_handlers
    assert Exception in app.exception_handlers
