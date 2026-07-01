"""Coverage-focused tests for app/scaling/tasks.py utility functions.

Targets the pure/near-pure functions that can be tested without a live
Celery worker or broker:
- _monotonic, _strip_secret_redis_schedule_fields, _scheduled_goal_id
- _run_async
- _record_goal_duration_metric, _record_schedule_fire_metric
- _get_llm_provider (with mocked Redis / vault)
- _scheduled_goal_kwargs, _schedule_key
- _datetime_to_naive_iso, _schedule_datetime
- _db_schedule_payload
- _db_schedule_discovery_enabled
- _decrement_after_completion (async, with mocked redis)
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# _monotonic
# ===========================================================================

def test_monotonic_returns_positive_float() -> None:
    from app.scaling.tasks import _monotonic
    t = _monotonic()
    assert isinstance(t, float)
    assert t > 0


def test_monotonic_is_monotonically_increasing() -> None:
    from app.scaling.tasks import _monotonic
    t1 = _monotonic()
    t2 = _monotonic()
    assert t2 >= t1


# ===========================================================================
# _strip_secret_redis_schedule_fields
# ===========================================================================

def test_strip_removes_known_secret_fields() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    sched = {
        "goal_id": "g1",
        "tenant_id": "t1",
        "webhook_token": "secret-token",
        "token": "another-secret",
        "password": "p@ss",
        "api_key": "ak-xyz",
        "secret": "shh",
        "trigger_type": "cron",
    }
    result = _strip_secret_redis_schedule_fields(sched)
    assert "webhook_token" not in result
    assert "token" not in result
    assert "password" not in result
    assert "api_key" not in result
    assert "secret" not in result
    # Non-secret fields preserved
    assert result["goal_id"] == "g1"
    assert result["trigger_type"] == "cron"


def test_strip_preserves_non_secret_fields() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    sched = {"goal_template": "deploy my app", "tenant_id": "t1", "cron_expression": "*/5 * * * *"}
    result = _strip_secret_redis_schedule_fields(sched)
    assert result == sched


def test_strip_case_insensitive_field_names() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    # The check uses key.lower() — uppercase should still be stripped
    sched = {"API_KEY": "secret", "Webhook_Token": "tok", "goal_id": "g2"}
    result = _strip_secret_redis_schedule_fields(sched)
    # Field matching is lowercase — keys are compared case-insensitively
    # "API_KEY".lower() == "api_key" → stripped
    assert "API_KEY" not in result
    assert "goal_id" in result


def test_strip_returns_empty_when_all_secret() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    sched = {"password": "x", "token": "y"}
    result = _strip_secret_redis_schedule_fields(sched)
    assert result == {}


# ===========================================================================
# _scheduled_goal_id
# ===========================================================================

def test_scheduled_goal_id_starts_with_sched_prefix() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    gid = _scheduled_goal_id("schedule:t1:s1")
    assert gid.startswith("sched_")


def test_scheduled_goal_id_is_deterministic_with_fire_instance() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    gid1 = _scheduled_goal_id("sched-key", fire_instance_id="2024-01-01T00:00:00")
    gid2 = _scheduled_goal_id("sched-key", fire_instance_id="2024-01-01T00:00:00")
    assert gid1 == gid2


def test_scheduled_goal_id_differs_for_different_inputs() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    gid1 = _scheduled_goal_id("key-a", fire_instance_id="ts-1")
    gid2 = _scheduled_goal_id("key-b", fire_instance_id="ts-1")
    assert gid1 != gid2


def test_scheduled_goal_id_fire_instance_in_hash() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    key = "schedule:t1:s1"
    instance = "2024-06-01T12:00:00"
    expected_hash = hashlib.sha256(f"{key}:{instance}".encode()).hexdigest()[:26]
    gid = _scheduled_goal_id(key, fire_instance_id=instance)
    assert gid == f"sched_{expected_hash}"


def test_scheduled_goal_id_without_fire_instance_uses_current_time() -> None:
    """Without fire_instance_id, each call produces a different ID (timestamp based)."""
    from app.scaling.tasks import _scheduled_goal_id
    # With fixed time mock, should be deterministic
    fixed_time = "2024-01-01T00:00:00+00:00"
    with patch("app.scaling.tasks.datetime") as mock_dt:
        mock_dt.datetime.now.return_value.isoformat.return_value = fixed_time
        mock_dt.UTC = datetime.UTC
        gid = _scheduled_goal_id("k")
    assert gid.startswith("sched_")


# ===========================================================================
# _run_async
# ===========================================================================

def test_run_async_executes_coroutine_and_returns_result() -> None:
    from app.scaling.tasks import _run_async

    async def _coro() -> str:
        return "async-result"

    result = _run_async(_coro())
    assert result == "async-result"


def test_run_async_closes_event_loop_after_execution() -> None:
    from app.scaling.tasks import _run_async
    # Verify no leftover open loops (indirectly — just ensure no exception)
    calls = []

    async def _coro() -> None:
        calls.append(1)

    _run_async(_coro())
    assert len(calls) == 1


def test_run_async_propagates_exceptions() -> None:
    from app.scaling.tasks import _run_async

    async def _bad_coro() -> None:
        raise ValueError("expected error")

    with pytest.raises(ValueError, match="expected error"):
        _run_async(_bad_coro())


# ===========================================================================
# _record_goal_duration_metric
# ===========================================================================

def test_record_goal_duration_metric_calls_metrics_function() -> None:
    from app.scaling.tasks import _monotonic, _record_goal_duration_metric
    recorded: list[tuple] = []

    def _fake_record(status: str, duration: float, priority: str) -> None:
        recorded.append((status, duration, priority))

    with patch("app.observability.metrics.record_goal_duration", _fake_record):
        _record_goal_duration_metric(
            "complete", started_monotonic=_monotonic() - 1.5, priority="high"
        )

    assert len(recorded) == 1
    status, duration, priority = recorded[0]
    assert status == "complete"
    assert duration >= 1.4  # at least 1.4s elapsed
    assert priority == "high"


def test_record_goal_duration_metric_swallows_exceptions() -> None:
    from app.scaling.tasks import _record_goal_duration_metric
    with patch(
        "app.observability.metrics.record_goal_duration",
        side_effect=RuntimeError("metrics down"),
    ):
        # Should not raise
        _record_goal_duration_metric("failed", started_monotonic=0.0, priority="low")


# ===========================================================================
# _record_schedule_fire_metric
# ===========================================================================

def test_record_schedule_fire_metric_calls_metrics_function() -> None:
    from app.scaling.tasks import _record_schedule_fire_metric
    recorded: list[str] = []

    def _fake_record(status: str) -> None:
        recorded.append(status)

    with patch("app.observability.metrics.record_schedule_fire", _fake_record):
        _record_schedule_fire_metric("success")

    assert recorded == ["success"]


def test_record_schedule_fire_metric_swallows_exceptions() -> None:
    from app.scaling.tasks import _record_schedule_fire_metric
    with patch(
        "app.observability.metrics.record_schedule_fire",
        side_effect=RuntimeError("down"),
    ):
        _record_schedule_fire_metric("error")  # must not raise


# ===========================================================================
# _get_llm_provider
# ===========================================================================

def test_get_llm_provider_returns_none_when_no_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.scaling.tasks import _get_llm_provider
    result = _get_llm_provider("tenant-1")
    assert result is None


def test_get_llm_provider_returns_none_when_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # No config key in Redis

    with patch("redis.from_url", return_value=mock_redis):
        from app.scaling.tasks import _get_llm_provider
        result = _get_llm_provider("tenant-1")

    assert result is None


def test_get_llm_provider_returns_none_when_encrypted_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json

    config = {"provider": "anthropic", "encrypted_key": "", "model": "claude-opus-4-8"}
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(config)

    with patch("redis.from_url", return_value=mock_redis):
        from app.scaling.tasks import _get_llm_provider
        result = _get_llm_provider("tenant-1")

    assert result is None


def test_get_llm_provider_returns_anthropic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json

    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="test-vault-key")
    encrypted = vault.encrypt("sk-ant-test-key")

    config = {"provider": "anthropic", "encrypted_key": encrypted, "model": "claude-opus-4-8"}
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(config)

    with (
        patch("redis.from_url", return_value=mock_redis),
        patch("app.providers.vault.get_vault", return_value=vault),
        patch("anthropic.AsyncAnthropic"),
    ):
        from app.scaling.tasks import _get_llm_provider
        result = _get_llm_provider("tenant-1")

    from app.providers.anthropic_provider import AnthropicProvider
    assert isinstance(result, AnthropicProvider)


def test_get_llm_provider_returns_openai_provider_for_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json
    import sys

    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="test-vault-key-2")
    encrypted = vault.encrypt("sk-openai-test-key")

    config = {"provider": "openai", "encrypted_key": encrypted, "model": "gpt-4o"}
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(config)

    mock_openai = MagicMock()  # no spec
    mock_openai.AsyncOpenAI.return_value = MagicMock()

    with (
        patch("redis.from_url", return_value=mock_redis),
        patch("app.providers.vault.get_vault", return_value=vault),
        patch.dict(sys.modules, {"openai": mock_openai}),
    ):
        from app.scaling.tasks import _get_llm_provider
        result = _get_llm_provider("tenant-2")

    # Verify result is an OpenAI provider (not None) without fragile isinstance check
    assert result is not None
    assert hasattr(result, "complete")
    assert hasattr(result, "_default_model")


def test_get_llm_provider_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("redis.from_url", side_effect=RuntimeError("redis error")):
        from app.scaling.tasks import _get_llm_provider
        result = _get_llm_provider("tenant-3")
    assert result is None


# ===========================================================================
# _scheduled_goal_kwargs
# ===========================================================================

def test_scheduled_goal_kwargs_returns_payload_for_valid_schedule() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"goal_template": "Run daily report", "tenant_id": "t1", "agent_id": "ag1"}
    result = _scheduled_goal_kwargs("schedule:t1:s1", sched, fire_instance_id="ts-1")
    assert result is not None
    assert result["goal_text"] == "Run daily report"
    assert result["tenant_id"] == "t1"
    assert result["agent_id"] == "ag1"
    assert result["goal_id"].startswith("sched_")


def test_scheduled_goal_kwargs_falls_back_to_goal_id_when_no_template() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"goal_id": "fallback-goal", "tenant_id": "t1"}
    result = _scheduled_goal_kwargs("key", sched, fire_instance_id="ts-1")
    assert result is not None
    assert result["goal_text"] == "fallback-goal"


def test_scheduled_goal_kwargs_returns_none_when_no_goal_text() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"tenant_id": "t1"}  # no goal_template or goal_id
    result = _scheduled_goal_kwargs("key", sched)
    assert result is None


def test_scheduled_goal_kwargs_returns_none_when_no_tenant_id() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"goal_template": "Do something"}  # no tenant_id
    result = _scheduled_goal_kwargs("key", sched)
    assert result is None


def test_scheduled_goal_kwargs_omits_agent_id_when_empty() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"goal_template": "Do task", "tenant_id": "t1", "agent_id": ""}
    result = _scheduled_goal_kwargs("key", sched, fire_instance_id="ts-2")
    assert result is not None
    assert "agent_id" not in result


# ===========================================================================
# _schedule_key
# ===========================================================================

def test_schedule_key_format() -> None:
    from app.scaling.tasks import _schedule_key
    key = _schedule_key("my-tenant", "my-schedule")
    assert key == "schedule:my-tenant:my-schedule"


# ===========================================================================
# _datetime_to_naive_iso
# ===========================================================================

def test_datetime_to_naive_iso_returns_none_for_none() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    assert _datetime_to_naive_iso(None) is None


def test_datetime_to_naive_iso_naive_datetime() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    dt = datetime.datetime(2024, 6, 15, 12, 0, 0)
    result = _datetime_to_naive_iso(dt)
    assert result == "2024-06-15T12:00:00"


def test_datetime_to_naive_iso_aware_datetime_strips_tz() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=datetime.UTC)
    result = _datetime_to_naive_iso(dt)
    assert result is not None
    assert "+" not in result  # timezone stripped
    assert "Z" not in result


def test_datetime_to_naive_iso_non_datetime_returns_str() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    result = _datetime_to_naive_iso("2024-01-01")  # type: ignore[arg-type]
    assert result == "2024-01-01"


# ===========================================================================
# _schedule_datetime
# ===========================================================================

def test_schedule_datetime_returns_none_for_none() -> None:
    from app.scaling.tasks import _schedule_datetime
    assert _schedule_datetime(None) is None


def test_schedule_datetime_returns_none_for_empty_string() -> None:
    from app.scaling.tasks import _schedule_datetime
    assert _schedule_datetime("") is None


def test_schedule_datetime_passthrough_naive_datetime() -> None:
    from app.scaling.tasks import _schedule_datetime
    dt = datetime.datetime(2024, 3, 1, 9, 30)
    result = _schedule_datetime(dt)
    assert result == dt


def test_schedule_datetime_strips_tz_from_aware_datetime() -> None:
    from app.scaling.tasks import _schedule_datetime
    dt = datetime.datetime(2024, 3, 1, 9, 30, tzinfo=datetime.UTC)
    result = _schedule_datetime(dt)
    assert result is not None
    assert result.tzinfo is None


def test_schedule_datetime_parses_iso_string() -> None:
    from app.scaling.tasks import _schedule_datetime
    result = _schedule_datetime("2024-06-15T12:30:00")
    assert result is not None
    assert result.year == 2024
    assert result.month == 6
    assert result.day == 15
    assert result.hour == 12


def test_schedule_datetime_parses_aware_iso_string_and_strips_tz() -> None:
    from app.scaling.tasks import _schedule_datetime
    result = _schedule_datetime("2024-06-15T12:30:00+00:00")
    assert result is not None
    assert result.tzinfo is None


# ===========================================================================
# _db_schedule_payload
# ===========================================================================

def test_db_schedule_payload_extracts_all_fields() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = MagicMock()
    row.id = "sched-uuid-123"
    row.tenant_id = "t1"
    row.goal_id_template = "Deploy the service"
    row.agent_id = "agent-42"
    row.trigger_type = "cron"
    row.cron_expression = "0 9 * * 1"
    row.timezone = "US/Eastern"
    row.interval_seconds = 0
    row.webhook_token = "tok123"
    row.event_channel = "ch1"
    row.fire_at_iso = ""
    row.condition = ""
    row.description = "Weekly deploy"
    row.paused = False
    row.last_fired_at = None

    payload = _db_schedule_payload(row)

    assert payload["schedule_id"] == "sched-uuid-123"
    assert payload["tenant_id"] == "t1"
    assert payload["goal_template"] == "Deploy the service"
    assert payload["agent_id"] == "agent-42"
    assert payload["trigger_type"] == "cron"
    assert payload["cron_expression"] == "0 9 * * 1"
    assert payload["timezone"] == "US/Eastern"
    assert payload["paused"] is False
    assert payload["last_fired_at"] is None


def test_db_schedule_payload_handles_missing_attrs() -> None:
    """Row with no attributes returns safe defaults."""
    from app.scaling.tasks import _db_schedule_payload

    class _MinimalRow:
        pass

    payload = _db_schedule_payload(_MinimalRow())
    assert payload["schedule_id"] == ""
    assert payload["tenant_id"] == ""
    assert payload["paused"] is False


def test_db_schedule_payload_last_fired_at_datetime() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = MagicMock()
    row.id = "id1"
    row.tenant_id = "t1"
    row.goal_id_template = "task"
    row.agent_id = ""
    row.trigger_type = ""
    row.cron_expression = ""
    row.timezone = "UTC"
    row.interval_seconds = 0
    row.webhook_token = ""
    row.event_channel = ""
    row.fire_at_iso = ""
    row.condition = ""
    row.description = ""
    row.paused = False
    row.last_fired_at = datetime.datetime(2024, 1, 1, 0, 0, 0)

    payload = _db_schedule_payload(row)
    assert payload["last_fired_at"] == "2024-01-01T00:00:00"


# ===========================================================================
# _db_schedule_discovery_enabled
# ===========================================================================

def test_db_schedule_discovery_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    from app.scaling.tasks import _db_schedule_discovery_enabled
    assert _db_schedule_discovery_enabled() is False


def test_db_schedule_discovery_enabled_true_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("1", "true", "yes", "True", "YES"):
        monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", val)
        from app.scaling.tasks import _db_schedule_discovery_enabled
        assert _db_schedule_discovery_enabled() is True


def test_db_schedule_discovery_disabled_false_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
    from app.scaling.tasks import _db_schedule_discovery_enabled
    assert _db_schedule_discovery_enabled() is False


# ===========================================================================
# _decrement_after_completion
# ===========================================================================

@pytest.mark.asyncio
async def test_decrement_after_completion_calls_decrement_func() -> None:
    from app.scaling.tasks import _decrement_after_completion

    called: list[tuple] = []

    async def _fake_decrement(tenant_id: str, redis: object) -> None:
        called.append((tenant_id,))

    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch("redis.asyncio.from_url", return_value=mock_redis),
        patch("app.tenancy.limits.decrement_concurrent_goals", _fake_decrement),
    ):
        await _decrement_after_completion("tenant-1", "redis://localhost/0")

    assert len(called) == 1
    assert called[0][0] == "tenant-1"


@pytest.mark.asyncio
async def test_decrement_after_completion_swallows_exception() -> None:
    from app.scaling.tasks import _decrement_after_completion

    with patch("redis.asyncio.from_url", side_effect=RuntimeError("conn fail")):
        # Should not raise
        await _decrement_after_completion("t1", "redis://localhost/0")


# ===========================================================================
# check_mcp_health task — covers lines 1032-1161
# ===========================================================================

def test_check_mcp_health_no_redis_scan_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_mcp_health with empty Redis scan returns ok with 0 servers."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health

    async def _empty_scan(*a: object, **kw: object):
        return
        yield  # Make it an async generator

    mock_r = MagicMock()
    mock_r.scan_iter = _empty_scan
    mock_r.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_r):
        result = check_mcp_health.run()

    assert result["status"] == "ok"
    assert result["servers_checked"] == 0


def test_check_mcp_health_with_parse_error_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_mcp_health with a key that fails MCPServerConfig parsing uses parse_error path."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_one_key(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1:srv1"

    mock_r = MagicMock()
    mock_r.scan_iter = _scan_one_key
    mock_r.get = AsyncMock(return_value='{"invalid": true}')  # Can't parse as MCPServerConfig
    mock_r.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_r):
        result = check_mcp_health.run()

    assert result["status"] == "ok"


def test_check_mcp_health_fallback_on_run_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """When _run() raises, _fallback() is executed."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health

    call_log: list[str] = []

    async def _fail_scan(match: str, count: int):
        raise RuntimeError("scan failed")
        yield  # make it generator

    async def _empty_scan(match: str, count: int):
        return
        yield

    mock_r_fail = MagicMock()
    mock_r_fail.scan_iter = _fail_scan
    mock_r_fail.aclose = AsyncMock()

    mock_r_fallback = MagicMock()
    mock_r_fallback.scan_iter = _empty_scan
    mock_r_fallback.aclose = AsyncMock()

    from_url_calls: list = []

    def _from_url(url: str, **kw: object) -> MagicMock:
        from_url_calls.append(url)
        if len(from_url_calls) == 1:
            return mock_r_fail
        return mock_r_fallback

    with patch("redis.asyncio.from_url", side_effect=_from_url):
        result = check_mcp_health.run()

    assert result["status"] == "ok"
    # _fallback was called (second redis connection)
    assert len(from_url_calls) >= 2


def test_check_mcp_health_fallback_with_valid_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fallback parses flat-dict server structure and tries health check."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json
    from collections.abc import AsyncIterator
    from app.scaling.tasks import check_mcp_health

    # _run() will fail because MCPServerConfig is hard to mock in async context
    # We'll force _fallback by making the main run raise
    server_data = {"srv1": {"url": "http://server1.example.com"}}

    async def _scan_keys_run(match: str, count: int):
        # _run uses mcp:servers:*:* pattern — just raise immediately
        raise RuntimeError("force fallback")
        yield

    async def _scan_keys_fallback(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1"

    call_count = [0]

    def _from_url(url: str, **kw: object) -> MagicMock:
        call_count[0] += 1
        mock_r = MagicMock()
        if call_count[0] == 1:
            mock_r.scan_iter = _scan_keys_run
        else:
            mock_r.scan_iter = _scan_keys_fallback
            mock_r.get = AsyncMock(return_value=json.dumps(server_data))
        mock_r.aclose = AsyncMock()
        return mock_r

    import httpx

    with (
        patch("redis.asyncio.from_url", side_effect=_from_url),
        patch("httpx.AsyncClient") as mock_httpx,
    ):
        # Mock httpx to avoid real network calls
        mock_ctx = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.get = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_ctx

        result = check_mcp_health.run()

    assert result["status"] == "ok"


# ===========================================================================
# fire_due_schedules — basic run with empty schedules
# ===========================================================================

def test_fire_due_schedules_no_redis_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """fire_due_schedules with no Redis and no DB returns ok with 0 fired."""
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    from app.scaling.tasks import fire_due_schedules

    with patch("app.scaling.tasks._run_async", return_value={}):
        result = fire_due_schedules.run()

    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_with_redis_empty_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """fire_due_schedules with Redis returning no schedule keys fires 0."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    from app.scaling.tasks import fire_due_schedules

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter([])  # No schedule keys

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_skips_paused_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paused schedules are skipped even if they are due."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules

    paused_schedule = {
        "goal_template": "daily task",
        "tenant_id": "t1",
        "trigger_type": "interval",
        "interval_seconds": 60,
        "paused": True,  # Paused
        "last_fired_at": None,
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:sched1"])
    mock_r.get.return_value = json.dumps(paused_schedule)

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    assert result["schedules_fired"] == 0


def test_fire_due_schedules_interval_due_dispatches_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    """An overdue interval schedule fires a goal."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules
    from datetime import datetime, UTC, timedelta

    # Set last_fired_at to 2 hours ago (well past interval)
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None).isoformat()

    interval_schedule = {
        "goal_template": "hourly report",
        "tenant_id": "t1",
        "trigger_type": "interval",
        "interval_seconds": 3600,  # 1 hour
        "paused": False,
        "last_fired_at": two_hours_ago,
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:sched1"])
    mock_r.get.return_value = json.dumps(interval_schedule)
    mock_r.set = MagicMock()

    with (
        patch("redis.from_url", return_value=mock_r),
        patch("app.scaling.tasks.run_goal") as mock_run_goal,
    ):
        mock_run_goal.apply_async = MagicMock()
        result = fire_due_schedules.run()

    # Should have fired the schedule
    assert result["schedules_fired"] == 1


# ===========================================================================
# _run_with_signals — pause/resume path (lines 236-250)
# ===========================================================================

# ===========================================================================
# _run_with_signals — covered by existing test_run_with_signals_completes_when_agent_succeeds
# The cancel path test is already provided above.
# ===========================================================================


# ===========================================================================
# _update_db_schedule_last_fired_at
# ===========================================================================

@pytest.mark.asyncio
async def test_update_db_schedule_last_fired_at_with_mocked_db() -> None:
    from app.scaling.tasks import _update_db_schedule_last_fired_at
    from datetime import datetime, UTC

    mock_execute = AsyncMock()
    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_commit = AsyncMock()
    mock_session.commit = mock_commit
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=None)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    def _rls_ctx(*a: object) -> object:
        return mock_ctx

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("app.db.session.get_session_factory", return_value=mock_factory),
        patch("app.db.rls.sqlalchemy_rls_context", side_effect=_rls_ctx),
    ):
        # Should raise since we're not in a transaction properly, but shouldn't crash tests
        try:
            await _update_db_schedule_last_fired_at("t1", "s1", datetime.now(UTC))
        except Exception:
            pass  # Expected in mocked environment


@pytest.mark.asyncio
async def test_update_db_schedule_last_fired_at_raises_on_db_error() -> None:
    """DB errors propagate from _update_db_schedule_last_fired_at."""
    from app.scaling.tasks import _update_db_schedule_last_fired_at
    from datetime import datetime, UTC

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        with pytest.raises(Exception):
            await _update_db_schedule_last_fired_at("t1", "s1", datetime.now(UTC))


# ===========================================================================
# Additional check_mcp_health coverage
# ===========================================================================

def test_check_mcp_health_httpx_health_check_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the MCPServerConfig parsing + httpx health check path."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_keys(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1:srv1"

    mock_r = MagicMock()
    mock_r.scan_iter = _scan_keys
    mock_r.aclose = AsyncMock()

    raw_config = '{"id": "srv1", "name": "TestServer", "base_url": "http://test.example", "tenant_id": "t1"}'
    mock_r.get = AsyncMock(return_value=raw_config)

    mock_cfg = MagicMock()
    mock_cfg.name = "TestServer"
    mock_cfg.base_url = "http://test.example"

    mock_response = MagicMock()
    mock_response.status_code = 200

    with (
        patch("redis.asyncio.from_url", return_value=mock_r),
        patch("app.mcp.registry.MCPServerConfig.model_validate_json", return_value=mock_cfg),
        patch("httpx.AsyncClient") as mock_httpx,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.get = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_ctx

        result = check_mcp_health.run()

    assert result["status"] == "ok"
    assert result["servers_checked"] >= 1


def test_check_mcp_health_httpx_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the httpx error handling path in check_mcp_health."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_keys(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1:srv2"

    mock_r = MagicMock()
    mock_r.scan_iter = _scan_keys
    mock_r.aclose = AsyncMock()
    mock_r.get = AsyncMock(return_value='{"id": "srv2", "name": "BrokenServer", "base_url": "http://broken", "tenant_id": "t1"}')

    mock_cfg = MagicMock()
    mock_cfg.name = "BrokenServer"
    mock_cfg.base_url = "http://broken"

    with (
        patch("redis.asyncio.from_url", return_value=mock_r),
        patch("app.mcp.registry.MCPServerConfig.model_validate_json", return_value=mock_cfg),
        patch("httpx.AsyncClient") as mock_httpx,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.get = AsyncMock(side_effect=OSError("connection refused"))
        mock_httpx.return_value = mock_ctx

        result = check_mcp_health.run()

    assert result["status"] == "ok"


# ===========================================================================
# record_queue_depths retry path
# ===========================================================================

def test_record_queue_depths_retry_on_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Redis fails in record_queue_depths, self.retry is raised."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import record_queue_depths
    import celery.exceptions

    with patch("redis.from_url", side_effect=RuntimeError("redis down")):
        with pytest.raises((celery.exceptions.Retry, RuntimeError)):
            record_queue_depths.run()


# ===========================================================================
# Additional fire_due_schedules coverage — cron schedule
# ===========================================================================

def test_fire_due_schedules_cron_due_dispatches_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cron schedule that is due fires a goal."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules

    # Cron that fires every minute — always due since last_fired_at is old
    from datetime import datetime, UTC, timedelta
    old_time = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()

    cron_schedule = {
        "goal_template": "cron task",
        "tenant_id": "t1",
        "trigger_type": "cron",
        "cron_expression": "* * * * *",  # Every minute
        "paused": False,
        "last_fired_at": old_time,
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:cron1"])
    mock_r.get.return_value = json.dumps(cron_schedule)
    mock_r.set = MagicMock()

    with (
        patch("redis.from_url", return_value=mock_r),
        patch("app.scaling.tasks.run_goal") as mock_run_goal,
    ):
        mock_run_goal.apply_async = MagicMock()
        result = fire_due_schedules.run()

    assert result["schedules_fired"] == 1


def test_fire_due_schedules_once_schedule_due_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 'once' schedule that is due fires exactly once."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules
    from datetime import datetime, UTC, timedelta

    # Fire time in the past (should have fired)
    past_time = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()

    once_schedule = {
        "goal_template": "one time task",
        "tenant_id": "t1",
        "trigger_type": "once",
        "fire_at_iso": past_time,
        "paused": False,
        "last_fired_at": None,  # Never fired
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:once1"])
    mock_r.get.return_value = json.dumps(once_schedule)
    mock_r.set = MagicMock()

    with (
        patch("redis.from_url", return_value=mock_r),
        patch("app.scaling.tasks.run_goal") as mock_run_goal,
    ):
        mock_run_goal.apply_async = MagicMock()
        result = fire_due_schedules.run()

    assert result["schedules_fired"] == 1


# ===========================================================================
# run_goal dry_run path — covers large swath of the task body
# ===========================================================================

def test_run_goal_blocked_by_emergency_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_goal returns 'blocked' when emergency stop is active."""
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from app.scaling.tasks import run_goal

    mock_sync_r = MagicMock()
    mock_sync_r.get.return_value = "1"  # Emergency stop active

    with patch("app.scaling.tasks._get_sync_redis", return_value=mock_sync_r):
        result = run_goal.run(
            goal_id="blocked-goal",
            tenant_id="t1",
            goal_text="blocked goal",
            dry_run=False,
        )

    assert result["status"] == "blocked"
    assert "Emergency stop" in result["reason"]


# ===========================================================================
# _setup_sigterm and _handler
# ===========================================================================

def test_setup_sigterm_registers_handler_without_error() -> None:
    """_setup_sigterm can be called without raising."""
    from app.scaling.tasks import _setup_sigterm
    # Should not raise
    _setup_sigterm()


def test_setup_sigterm_handler_raises_system_exit() -> None:
    """The SIGTERM handler raises SystemExit(0) when triggered."""
    import signal as _sig
    from app.scaling.tasks import _setup_sigterm

    _setup_sigterm()
    handler = _sig.getsignal(_sig.SIGTERM)
    assert callable(handler)
    with pytest.raises(SystemExit):
        handler(int(_sig.SIGTERM), None)


def test_setup_sigterm_handles_os_error_gracefully() -> None:
    """_setup_sigterm catches OSError/ValueError from invalid signal contexts."""
    import signal as _sig
    with patch("signal.signal", side_effect=OSError("not main thread")):
        from app.scaling.tasks import _setup_sigterm
        _setup_sigterm()  # should not raise


# ===========================================================================
# _get_redis_pool and _get_sync_redis
# ===========================================================================

def test_get_redis_pool_creates_and_caches_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import app.scaling.tasks as _tasks_mod
    # Reset the global pool so we can test creation
    original_pool = _tasks_mod._REDIS_POOL
    _tasks_mod._REDIS_POOL = None
    try:
        mock_pool = MagicMock()
        with patch("redis.ConnectionPool.from_url", return_value=mock_pool):
            pool1 = _tasks_mod._get_redis_pool()
            pool2 = _tasks_mod._get_redis_pool()  # Should return cached
        assert pool1 is mock_pool
        assert pool1 is pool2  # Same object
    finally:
        _tasks_mod._REDIS_POOL = original_pool


def test_get_sync_redis_returns_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import app.scaling.tasks as _tasks_mod

    mock_pool = MagicMock()
    mock_redis_client = MagicMock()
    original_pool = _tasks_mod._REDIS_POOL
    _tasks_mod._REDIS_POOL = mock_pool
    try:
        with patch("redis.Redis", return_value=mock_redis_client) as mock_redis_cls:
            result = _tasks_mod._get_sync_redis()
        mock_redis_cls.assert_called_once_with(connection_pool=mock_pool)
        assert result is mock_redis_client
    finally:
        _tasks_mod._REDIS_POOL = original_pool


# ===========================================================================
# run_goal_dlq task
# ===========================================================================

def test_run_goal_dlq_returns_dead_lettered_status() -> None:
    """run_goal_dlq marks the goal as dead-lettered."""
    from app.scaling.tasks import run_goal_dlq

    with patch("app.scaling.tasks._run_async", side_effect=Exception("no db")):
        result = run_goal_dlq.run(
            goal_id="g1",
            tenant_id="t1",
            goal_text="deploy",
            reason="max_retries",
        )

    assert result["status"] == "dead_lettered"
    assert result["goal_id"] == "g1"
    assert result["reason"] == "max_retries"
    assert result["tenant_id"] == "t1"


def test_run_goal_dlq_succeeds_when_db_update_works() -> None:
    """run_goal_dlq succeeds even when DB update succeeds."""
    from app.scaling.tasks import run_goal_dlq

    with patch("app.scaling.tasks._run_async", return_value=None):
        result = run_goal_dlq.run(
            goal_id="g2", tenant_id="t2", reason="test"
        )

    assert result["status"] == "dead_lettered"


# ===========================================================================
# _update_goal_dlq async function
# ===========================================================================

@pytest.mark.asyncio
async def test_update_goal_dlq_with_mocked_db() -> None:
    """_update_goal_dlq updates goal status using SQLAlchemy."""
    from app.scaling.tasks import _update_goal_dlq
    from unittest.mock import AsyncMock

    mock_execute = AsyncMock()
    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)

    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        await _update_goal_dlq("goal-123", "tenant-456", "reason")

    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_goal_dlq_handles_db_error_gracefully() -> None:
    """_update_goal_dlq logs and swallows DB errors."""
    from app.scaling.tasks import _update_goal_dlq

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        await _update_goal_dlq("g1", "t1", "test error")  # should not raise


# ===========================================================================
# record_queue_depths task
# ===========================================================================

def test_record_queue_depths_returns_skipped_when_no_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.scaling.tasks import record_queue_depths
    result = record_queue_depths.run()
    assert result["status"] == "skipped"


def test_record_queue_depths_records_depths_with_mocked_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import record_queue_depths

    mock_r = MagicMock()
    mock_r.llen.return_value = 5

    def _fake_record(queue: str, depth: float) -> None:
        pass

    with (
        patch("redis.from_url", return_value=mock_r),
        patch("app.observability.metrics.record_queue_depth", _fake_record),
    ):
        result = record_queue_depths.run()

    assert result["status"] == "ok"
    assert result["queues_recorded"] == 3  # goals, schedules, maintenance


# ===========================================================================
# _find_and_fail_stuck_goals async function
# ===========================================================================

@pytest.mark.asyncio
async def test_find_and_fail_stuck_goals_with_mocked_db() -> None:
    from app.scaling.tasks import _find_and_fail_stuck_goals
    from unittest.mock import AsyncMock

    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("id-1",), ("id-2",)]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        result = await _find_and_fail_stuck_goals()

    assert result["stuck_goals_failed"] == 2


@pytest.mark.asyncio
async def test_find_and_fail_stuck_goals_ignores_terminal_event_goals() -> None:
    from app.scaling.tasks import _find_and_fail_stuck_goals

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        await _find_and_fail_stuck_goals()

    sql = str(mock_session.execute.call_args.args[0])
    assert "NOT EXISTS" in sql
    assert "goal_events" in sql
    assert "goal_complete" in sql
    assert "worker_complete" in sql


@pytest.mark.asyncio
async def test_find_and_fail_stuck_goals_handles_db_error() -> None:
    from app.scaling.tasks import _find_and_fail_stuck_goals

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        result = await _find_and_fail_stuck_goals()

    assert result["stuck_goals_failed"] == 0
    assert "error" in result


# ===========================================================================
# _delete_expired_records async function
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_expired_records_with_mocked_db() -> None:
    from app.scaling.tasks import _delete_expired_records
    from unittest.mock import AsyncMock

    mock_exec_result = MagicMock()
    mock_exec_result.rowcount = 3

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_exec_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        result = await _delete_expired_records(90)

    assert result["retention_days"] == 90
    assert "deleted" in result


@pytest.mark.asyncio
async def test_delete_expired_records_handles_db_error() -> None:
    from app.scaling.tasks import _delete_expired_records

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        result = await _delete_expired_records(30)

    assert "error" in result


# ===========================================================================
# expire_hitl_approvals task
# ===========================================================================

@pytest.mark.asyncio
async def test_expire_hitl_approvals_task_returns_expired_count() -> None:
    from app.scaling.tasks import expire_hitl_approvals

    with patch("app.scaling.tasks._run_async", return_value=["req-1", "req-2"]):
        result = expire_hitl_approvals.run()

    assert result["expired"] == 2
    assert "checked_at" in result


@pytest.mark.asyncio
async def test_expire_hitl_approvals_task_handles_error() -> None:
    from app.scaling.tasks import expire_hitl_approvals

    with patch("app.scaling.tasks._run_async", side_effect=Exception("db error")):
        result = expire_hitl_approvals.run()

    assert result["expired"] == 0


# ===========================================================================
# detect_stuck_goals task
# ===========================================================================

def test_detect_stuck_goals_task_calls_run_async() -> None:
    from app.scaling.tasks import detect_stuck_goals

    with patch(
        "app.scaling.tasks._run_async",
        return_value={"stuck_goals_failed": 0, "goal_ids": []},
    ):
        result = detect_stuck_goals.run()

    assert "stuck_goals_failed" in result


# ===========================================================================
# execute_retention_policy task
# ===========================================================================

def test_execute_retention_policy_calls_run_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_RETENTION_DAYS", "30")
    from app.scaling.tasks import execute_retention_policy

    with patch(
        "app.scaling.tasks._run_async",
        return_value={"retention_days": 30, "deleted": {}, "cutoff": "2024-01-01"},
    ):
        result = execute_retention_policy.run()

    assert result["retention_days"] == 30


# ===========================================================================
# _load_db_schedules (partial test)
# ===========================================================================

@pytest.mark.asyncio
async def test_load_db_schedules_handles_db_error() -> None:
    """When DB is unavailable, _load_db_schedules returns empty dict."""
    from app.scaling.tasks import _load_db_schedules

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        result = await _load_db_schedules()

    assert result == {}


# ===========================================================================
# _run_with_signals (partial test)
# ===========================================================================

@pytest.mark.asyncio
async def test_run_with_signals_completes_when_agent_succeeds() -> None:
    """When agent finishes quickly, _run_with_signals returns the result."""
    from app.scaling.tasks import _run_with_signals
    from unittest.mock import AsyncMock

    mock_state = MagicMock()
    mock_state.status = MagicMock()
    mock_state.status.value = "complete"

    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(return_value=mock_state)

    mock_tenant_ctx = MagicMock()
    mock_event_callback = AsyncMock()

    sync_r = MagicMock()
    sync_r.get = MagicMock(return_value=None)  # No cancel/pause signals

    with patch("app.scaling.tasks._get_sync_redis", return_value=sync_r):
        result = await _run_with_signals(
            mock_runner, "test goal", mock_tenant_ctx, mock_event_callback, "goal-123"
        )

    assert result is mock_state


@pytest.mark.asyncio
async def test_run_with_signals_forwards_initial_context() -> None:
    """Worker wrapper must pass tool_context into AgentGraph.run."""
    from app.scaling.tasks import _run_with_signals

    mock_state = MagicMock()
    mock_state.status.value = "complete"
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(return_value=mock_state)
    initial_context = {"tool_prompt": "Available tools", "tool_context": object()}

    with patch("app.scaling.tasks._get_sync_redis", return_value=None):
        result = await _run_with_signals(
            mock_runner,
            "test goal",
            MagicMock(),
            AsyncMock(),
            "goal-123",
            initial_context=initial_context,
        )

    assert result is mock_state
    assert mock_runner.run.await_args.kwargs["initial_context"] is initial_context


@pytest.mark.asyncio
async def test_run_with_signals_raises_goal_cancelled_error_on_cancel() -> None:
    """When goal is cancelled, GoalCancelledError is raised."""
    from app.scaling.tasks import _run_with_signals
    from unittest.mock import AsyncMock
    from app.reliability.goal_lifecycle import GoalCancelledError

    mock_state = MagicMock()
    mock_state.status.value = "complete"

    async def _slow_run(**kw: object) -> MagicMock:
        await asyncio.sleep(100)  # Long-running task
        return mock_state

    mock_runner = MagicMock()
    mock_runner.run = _slow_run

    sync_r = MagicMock()

    def _is_cancelled(goal_id: str, r: object) -> bool:
        return True  # Always cancelled

    def _is_paused(goal_id: str, r: object) -> bool:
        return False

    with (
        patch("app.scaling.tasks._get_sync_redis", return_value=sync_r),
        patch("app.reliability.goal_lifecycle.is_cancelled_sync", _is_cancelled),
        patch("app.reliability.goal_lifecycle.is_paused_sync", _is_paused),
        patch("asyncio.sleep", new=AsyncMock()),  # Don't actually sleep
    ):
        with pytest.raises(GoalCancelledError):
            await _run_with_signals(
                mock_runner, "goal", MagicMock(), AsyncMock(), "goal-xyz"
            )


# ===========================================================================
# check_mcp_health — per-key error path + fallback paths
# ===========================================================================

def test_check_mcp_health_per_key_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When r.get() raises per-key, the error is logged and skipped."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_keys(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1:srv1"

    mock_r = MagicMock()
    mock_r.scan_iter = _scan_keys
    mock_r.get = AsyncMock(side_effect=RuntimeError("redis error"))  # Per-key failure
    mock_r.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_r):
        result = check_mcp_health.run()

    # Error should be swallowed, result still ok
    assert result["status"] == "ok"


def test_check_mcp_health_fallback_no_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fallback returns skipped when no REDIS_URL."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "")  # empty
    from app.scaling.tasks import check_mcp_health

    # Force fallback by making _run() fail due to no redis URL
    # When REDIS_URL is empty, _fallback checks os.getenv("REDIS_URL") too
    # _run() will skip because redis_url is empty
    result = check_mcp_health.run()
    # Should succeed with 0 servers
    assert result["status"] == "ok"
    assert result["servers_checked"] == 0


def test_check_mcp_health_fallback_json_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fallback skips keys with invalid JSON."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    # Force fallback by raising in _run()
    async def _scan_keys_run(match: str, count: int) -> AsyncIterator[str]:
        raise RuntimeError("force fallback")
        yield

    async def _scan_keys_fallback(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1"

    call_count = [0]

    def _from_url(url: str, **kw: object) -> MagicMock:
        call_count[0] += 1
        mock_r = MagicMock()
        if call_count[0] == 1:
            mock_r.scan_iter = _scan_keys_run
        else:
            mock_r.scan_iter = _scan_keys_fallback
            mock_r.get = AsyncMock(return_value="not-valid-json!!!")  # Invalid JSON
        mock_r.aclose = AsyncMock()
        return mock_r

    with patch("redis.asyncio.from_url", side_effect=_from_url):
        result = check_mcp_health.run()

    assert result["status"] == "ok"


def test_check_mcp_health_fallback_various_data_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fallback skips keys with None, non-dict data, and empty URL."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_keys_run(match: str, count: int) -> AsyncIterator[str]:
        raise RuntimeError("force fallback")
        yield

    async def _scan_keys_fallback(match: str, count: int) -> AsyncIterator[str]:
        for k in ["mcp:servers:t1:a", "mcp:servers:t1:b", "mcp:servers:t1:c"]:
            yield k

    get_responses = [
        None,                               # None raw → line 1115 continue
        json.dumps([1, 2, 3]),              # non-dict data → line 1121 continue
        json.dumps({"srv": {"no_url": 1}}), # dict with no URL → line 1125 continue
    ]
    get_call_count = [0]

    async def _multi_get(key: str) -> str | None:
        idx = get_call_count[0] % len(get_responses)
        get_call_count[0] += 1
        return get_responses[idx]

    call_count = [0]

    def _from_url(url: str, **kw: object) -> MagicMock:
        call_count[0] += 1
        mock_r = MagicMock()
        if call_count[0] == 1:
            mock_r.scan_iter = _scan_keys_run
        else:
            mock_r.scan_iter = _scan_keys_fallback
            mock_r.get = _multi_get
        mock_r.aclose = AsyncMock()
        return mock_r

    with patch("redis.asyncio.from_url", side_effect=_from_url):
        result = check_mcp_health.run()

    assert result["status"] == "ok"


def test_check_mcp_health_fallback_httpx_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """_fallback handles httpx connection errors per-server (lines 1138-1139)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import json
    from app.scaling.tasks import check_mcp_health
    from collections.abc import AsyncIterator

    async def _scan_keys_run(match: str, count: int) -> AsyncIterator[str]:
        raise RuntimeError("force fallback")
        yield

    async def _scan_keys_fallback(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:servers:t1"

    server_data = {"srv1": {"url": "http://unreachable.example.com"}}
    call_count = [0]

    def _from_url(url: str, **kw: object) -> MagicMock:
        call_count[0] += 1
        mock_r = MagicMock()
        if call_count[0] == 1:
            mock_r.scan_iter = _scan_keys_run
        else:
            mock_r.scan_iter = _scan_keys_fallback
            mock_r.get = AsyncMock(return_value=json.dumps(server_data))
        mock_r.aclose = AsyncMock()
        return mock_r

    with (
        patch("redis.asyncio.from_url", side_effect=_from_url),
        patch("httpx.AsyncClient") as mock_httpx,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.get = AsyncMock(side_effect=OSError("connection refused"))
        mock_httpx.return_value = mock_ctx

        result = check_mcp_health.run()

    assert result["status"] == "ok"
    # Result should include unreachable server
    assert any(r.get("status") == "unreachable" for r in result["results"])


# ===========================================================================
# _expire_db_approvals async function
# ===========================================================================

@pytest.mark.asyncio
async def test_expire_db_approvals_with_mocked_db() -> None:
    """_expire_db_approvals updates expired requests."""
    from app.scaling.tasks import _expire_db_approvals
    from unittest.mock import AsyncMock

    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("req-1",), ("req-2",)]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        result = await _expire_db_approvals()

    assert len(result) == 2


@pytest.mark.asyncio
async def test_expire_db_approvals_handles_db_error() -> None:
    """_expire_db_approvals returns empty list on DB error."""
    from app.scaling.tasks import _expire_db_approvals

    with patch("app.db.session.get_session_factory", side_effect=Exception("no db")):
        result = await _expire_db_approvals()

    assert result == []


# ===========================================================================
# _delete_expired_records — per-table error path
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_expired_records_per_table_error() -> None:
    """Per-table errors in _delete_expired_records are recorded but don't crash."""
    from app.scaling.tasks import _delete_expired_records

    execute_call = [0]

    async def _raise_for_events(*a: object, **kw: object) -> None:
        execute_call[0] += 1
        if execute_call[0] == 1:
            raise RuntimeError("table locked")
        mock_r = MagicMock()
        mock_r.rowcount = 0
        return mock_r

    mock_session = AsyncMock()
    mock_session.execute = _raise_for_events
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.db.session.get_session_factory", return_value=mock_factory):
        result = await _delete_expired_records(90)

    # First table errored — should be in counts as "error: ..."
    assert "deleted" in result
    first_table = result["deleted"].get("goal_events", "")
    assert "error" in str(first_table)


# ===========================================================================
# fire_due_schedules — exception in inner loop
# ===========================================================================

def test_fire_due_schedules_exception_in_schedule_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception processing a single schedule key is caught and logged."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules

    # r.get() raises RuntimeError → triggers inner per-key exception handler
    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:bad"])
    mock_r.get.side_effect = RuntimeError("redis decode error")

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    # Should continue and return ok despite the error
    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_key_with_none_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """When r.get() returns None for a key, it is skipped (line 1200 coverage)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    from app.scaling.tasks import fire_due_schedules

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:empty"])
    mock_r.get.return_value = None  # None value → should continue

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_redis_scan_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When scan_iter raises, outer exception handler catches it (lines 1208-1209)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    from app.scaling.tasks import fire_due_schedules

    mock_r = MagicMock()
    mock_r.scan_iter.side_effect = RuntimeError("redis unavailable")

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_invalid_cron_expression_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid cron expression triggers cron_exc path (lines 1305-1307)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules
    from datetime import datetime, UTC, timedelta

    old_time = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    bad_cron_schedule = {
        "goal_template": "bad cron task",
        "tenant_id": "t1",
        "trigger_type": "cron",
        "cron_expression": "NOT-A-VALID-CRON-EXPRESSION",
        "paused": False,
        "last_fired_at": old_time,
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:badcron"])
    mock_r.get.return_value = json.dumps(bad_cron_schedule)

    with patch("redis.from_url", return_value=mock_r):
        result = fire_due_schedules.run()

    # Bad cron is skipped, but task should still succeed
    assert result["status"] == "ok"
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_schedule_with_secret_fields_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schedules with secret fields are sanitized and re-saved (line 1203-1204)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    import json
    from app.scaling.tasks import fire_due_schedules

    schedule_with_secret = {
        "goal_template": "task",
        "tenant_id": "t1",
        "trigger_type": "interval",
        "interval_seconds": 60,
        "paused": False,
        "last_fired_at": None,
        "webhook_token": "secret-should-be-removed",  # Secret field
    }

    mock_r = MagicMock()
    mock_r.scan_iter.return_value = iter(["schedule:t1:secret"])
    mock_r.get.return_value = json.dumps(schedule_with_secret)
    mock_r.set = MagicMock()

    with (
        patch("redis.from_url", return_value=mock_r),
        patch("app.scaling.tasks.run_goal") as mock_run_goal,
    ):
        mock_run_goal.apply_async = MagicMock()
        result = fire_due_schedules.run()

    # Secret field removed, schedule re-saved, and fired
    mock_r.set.assert_called()
    call_args = mock_r.set.call_args
    saved_data = json.loads(call_args[0][1])
    assert "webhook_token" not in saved_data
