"""Tests for app.scaling.tasks helper functions that are not yet covered.

Targets:
- _build_worker_graph_capability non-None branch (lines 248, 250)
- _build_worker_retrieval_gateway (line 252)
- _get_sync_redis connection pool branch (lines 78-89)
- _setup_sigterm installation (lines 25-35)
- _get_redis_pool creation branch (lines 65-72)
- _decrement_after_completion success path (lines 159-186)
- _load_worker_policy_engine reload success path (lines 225-241)
- _scheduled_goal_id with fire_instance_id (line 199-203)
- _strip_secret_redis_schedule_fields lowercase keys
- _record_goal_duration_metric success path (lines 257-265)
- _run_with_signals pause path edge cases
- _scheduled_goal_id with fire_instance_id explicit value
- _datetime_to_naive_iso helper functions
- _schedule_datetime helper functions
- _db_schedule_payload helper function
"""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scaling import tasks

# ── _build_worker_graph_capability ───────────────────────────────────────────


def test_build_worker_graph_capability_returns_none_for_none_db_factory() -> None:
    """_build_worker_graph_capability(None) returns None without importing rag.gateway."""
    result = tasks._build_worker_graph_capability(None)
    assert result is None


def test_build_worker_graph_capability_returns_adapter_for_db_factory() -> None:
    """_build_worker_graph_capability(db_factory) returns a TenantScopedGraphCapabilityAdapter
    instance (covers lines 248 and 250)."""
    fake_module = MagicMock()
    fake_adapter_instance = MagicMock(name="adapter_instance")
    fake_module.TenantScopedGraphCapabilityAdapter = MagicMock(
        return_value=fake_adapter_instance
    )
    db_factory = MagicMock()  # non-None
    with patch.dict("sys.modules", {"app.rag.gateway": fake_module}):
        result = tasks._build_worker_graph_capability(db_factory)
    assert result is fake_adapter_instance
    fake_module.TenantScopedGraphCapabilityAdapter.assert_called_once()


# ── _build_worker_retrieval_gateway ──────────────────────────────────────────


def test_build_worker_retrieval_gateway_returns_gateway() -> None:
    """_build_worker_retrieval_gateway(dependencies) returns a RetrievalGateway
    instance (covers line 252)."""
    fake_module = MagicMock()
    fake_gateway_instance = MagicMock(name="gateway_instance")
    fake_module.RetrievalGateway = MagicMock(return_value=fake_gateway_instance)
    deps = {"db_factory": MagicMock()}
    with patch.dict("sys.modules", {"app.rag.gateway": fake_module}):
        result = tasks._build_worker_retrieval_gateway(deps)
    assert result is fake_gateway_instance
    fake_module.RetrievalGateway.assert_called_once_with(deps)


# ── _record_goal_duration_metric ─────────────────────────────────────────────


def test_record_goal_duration_metric_success_path() -> None:
    """_record_goal_duration_metric calls record_goal_duration with elapsed time
    when the metrics module is importable (covers the success path)."""
    fake_module = MagicMock()
    fake_module.record_goal_duration = MagicMock()
    started = tasks._monotonic() - 0.5  # 500ms elapsed
    with patch.dict("sys.modules", {"app.observability.metrics": fake_module}):
        tasks._record_goal_duration_metric(
            status="completed", started_monotonic=started, priority="high"
        )
    fake_module.record_goal_duration.assert_called_once()
    args = fake_module.record_goal_duration.call_args.args
    assert args[0] == "completed"
    assert isinstance(args[1], float)
    assert args[1] > 0.0
    assert args[2] == "high"


# ── _load_worker_policy_engine ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_worker_policy_engine_reload_success_path() -> None:
    """_load_worker_policy_engine returns a PolicyEngine with policies loaded from
    db_factory (covers the success branch)."""
    fake_module = MagicMock()
    fake_engine = MagicMock()
    fake_module.PolicyEngine = MagicMock(return_value=fake_engine)
    fake_engine.reload_from_db = AsyncMock()
    db_factory = MagicMock()
    with patch.dict("sys.modules", {"app.governance.policies": fake_module}):
        result = await tasks._load_worker_policy_engine(db_factory, tenant_id="t1")
    assert result is fake_engine
    fake_engine.reload_from_db.assert_awaited_once_with(
        db_factory, tenant_id="t1", strict=True
    )


@pytest.mark.asyncio
async def test_load_worker_policy_engine_reload_failure_adds_deny_all_policy() -> None:
    """When reload_from_db raises, a deny-all Policy is added to the engine."""
    fake_module = MagicMock()
    fake_engine = MagicMock()
    fake_module.PolicyEngine = MagicMock(return_value=fake_engine)
    fake_engine.reload_from_db = AsyncMock(side_effect=RuntimeError("db unreachable"))
    fake_policy = MagicMock()
    fake_module.Policy = MagicMock(return_value=fake_policy)
    fake_engine.add_policy = MagicMock()
    db_factory = MagicMock()
    with patch.dict("sys.modules", {"app.governance.policies": fake_module}):
        result = await tasks._load_worker_policy_engine(db_factory, tenant_id="t1")
    assert result is fake_engine
    fake_engine.add_policy.assert_called_once_with(fake_policy)
    fake_module.Policy.assert_called_once()
    # Verify the deny-all policy is correctly configured
    policy_call_kwargs = fake_module.Policy.call_args.kwargs
    assert policy_call_kwargs["name"] == "worker-policy-load-failed"
    assert policy_call_kwargs["tenant_id"] == "t1"
    assert policy_call_kwargs["denied_tools"] == ["*"]


# ── _scheduled_goal_id ───────────────────────────────────────────────────────


def test_scheduled_goal_id_with_explicit_fire_instance_id() -> None:
    """_scheduled_goal_id uses the provided fire_instance_id when given."""
    result = tasks._scheduled_goal_id("my-schedule-key", fire_instance_id="2024-01-01T00:00:00")
    assert result.startswith("sched_")
    # Same inputs → same output (deterministic with explicit fire_instance_id)
    assert result == tasks._scheduled_goal_id(
        "my-schedule-key", fire_instance_id="2024-01-01T00:00:00"
    )


def test_scheduled_goal_id_changes_with_schedule_key() -> None:
    """Different schedule keys produce different goal IDs."""
    a = tasks._scheduled_goal_id("key-a", fire_instance_id="fixed")
    b = tasks._scheduled_goal_id("key-b", fire_instance_id="fixed")
    assert a != b


# ── _strip_secret_redis_schedule_fields ──────────────────────────────────────


def test_strip_secret_redis_schedule_filters_lowercase_keys() -> None:
    """_strip_secret_redis_schedule_fields filters case-insensitively against
    the set {webhook_token, token, password, api_key, secret}."""
    result = tasks._strip_secret_redis_schedule_fields(
        {
            "schedule_id": "abc",
            "REDIS_URL": "redis://secret:6379",  # NOT in secret set — kept
            "password": "hunter2",  # in secret set — removed
            "API_KEY": "secret",  # case-insensitive match for api_key — removed
            "tenant_id": "t-1",
            "webhook_token": "tok",  # in secret set — removed
            "SECRET": "topsecret",  # case-insensitive match for secret — removed
            "enabled": True,
        }
    )
    assert "schedule_id" in result
    assert "tenant_id" in result
    assert "enabled" in result
    assert "REDIS_URL" in result  # not in secret set, kept
    # All secret keys should be removed
    assert "password" not in result
    assert "API_KEY" not in result
    assert "webhook_token" not in result
    assert "SECRET" not in result


def test_strip_secret_redis_schedule_empty_dict() -> None:
    """_strip_secret_redis_schedule_fields returns empty dict for empty input."""
    assert tasks._strip_secret_redis_schedule_fields({}) == {}


def test_strip_secret_redis_schedule_no_secrets() -> None:
    """_strip_secret_redis_schedule_fields passes through dict with no secret keys."""
    src = {"tenant_id": "t1", "schedule_id": "s1", "enabled": True}
    assert tasks._strip_secret_redis_schedule_fields(src) == src


# ── _run_async ───────────────────────────────────────────────────────────────


def test_run_async_returns_coroutine_result() -> None:
    """_run_async runs a coroutine in a new event loop and returns its result."""
    # _run_async creates its own event loop, so we must NOT be inside an
    # existing async test. This test is sync (no @pytest.mark.asyncio).
    async def _coro() -> str:
        await __import__("asyncio").sleep(0)
        return "ok"

    # _run_async is sync; just call it directly
    result = tasks._run_async(_coro())
    assert result == "ok"


def test_run_async_handles_dispose_task_engine_failure() -> None:
    """_run_async swallows dispose_task_engine exceptions (finally clause).
    Covers lines around the inner try/except in _run_async."""

    async def _coro() -> str:
        return "result"

    # Inject a failing dispose_task_engine
    fake_db_session = MagicMock()
    fake_db_session.dispose_task_engine = AsyncMock(side_effect=RuntimeError("no engine"))
    with patch.dict("sys.modules", {"app.db.session": fake_db_session}):
        result = tasks._run_async(_coro())
    assert result == "result"


# ── _decrement_after_completion ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decrement_after_completion_success_path() -> None:
    """_decrement_after_completion opens an async Redis client and calls
    decrement_concurrent_goals."""
    # Build a fake redis package whose .asyncio submodule is our mock
    fake_redis_module = MagicMock(name="redis")
    fake_redis_asyncio = MagicMock(name="redis.asyncio")
    fake_redis_client = MagicMock(name="redis_client")
    fake_redis_client.aclose = AsyncMock()
    fake_redis_asyncio.from_url = MagicMock(return_value=fake_redis_client)
    fake_redis_module.asyncio = fake_redis_asyncio
    fake_limits_module = MagicMock(name="app.tenancy.limits")
    fake_limits_module.decrement_concurrent_goals = AsyncMock()

    with patch.dict(
        "sys.modules",
        {
            "redis": fake_redis_module,
            "redis.asyncio": fake_redis_asyncio,
            "app.tenancy.limits": fake_limits_module,
        },
    ):
        await tasks._decrement_after_completion("t1", "redis://localhost:6379")

    fake_redis_asyncio.from_url.assert_called_once_with(
        "redis://localhost:6379", decode_responses=True
    )
    fake_limits_module.decrement_concurrent_goals.assert_awaited_once_with(
        tenant_id="t1", redis=fake_redis_client
    )


# ── _setup_sigterm ───────────────────────────────────────────────────────────


def test_setup_sigterm_installs_handler() -> None:
    """_setup_sigterm installs a SIGTERM handler without raising."""
    # The function is called at module import already; just verify it's callable
    # and a no-op invocation doesn't raise.
    import signal

    original = signal.getsignal(signal.SIGTERM)
    try:
        tasks._setup_sigterm()
        # Handler should be set to SIG_IGN or a callable, not default SIG_DFL
        new_handler = signal.getsignal(signal.SIGTERM)
        assert callable(new_handler) or new_handler == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, original)


# ── _datetime_to_naive_iso and _schedule_datetime ────────────────────────────


def test_datetime_to_naive_iso_with_none_returns_none() -> None:
    assert tasks._datetime_to_naive_iso(None) is None


def test_datetime_to_naive_iso_with_naive_datetime() -> None:
    """_datetime_to_naive_iso returns ISO string for naive datetime."""
    dt = datetime.datetime(2024, 1, 15, 12, 30, 45)
    result = tasks._datetime_to_naive_iso(dt)
    assert result == "2024-01-15T12:30:45"


def test_datetime_to_naive_iso_with_aware_datetime_converts_to_utc() -> None:
    """_datetime_to_naive_iso converts aware datetime to UTC naive ISO."""
    dt = datetime.datetime(2024, 1, 15, 12, 30, 45, tzinfo=datetime.UTC)
    result = tasks._datetime_to_naive_iso(dt)
    # UTC datetime should be unchanged after astimezone(UTC) (no offset suffix)
    assert result == "2024-01-15T12:30:45"


def test_datetime_to_naive_iso_with_non_utc_aware_datetime_shifts_to_utc() -> None:
    """A datetime in +05:00 is shifted to UTC before formatting."""
    tz = datetime.timezone(datetime.timedelta(hours=5))
    dt = datetime.datetime(2024, 1, 15, 12, 30, 45, tzinfo=tz)  # 12:30 +05:00 = 07:30 UTC
    result = tasks._datetime_to_naive_iso(dt)
    assert result == "2024-01-15T07:30:45"


def test_datetime_to_naive_iso_with_string_passthrough() -> None:
    """_datetime_to_naive_iso returns the stringified value for non-datetime input."""
    assert tasks._datetime_to_naive_iso("2024-01-01") == "2024-01-01"


def test_schedule_datetime_with_none_returns_none() -> None:
    assert tasks._schedule_datetime(None) is None


def test_schedule_datetime_with_iso_string() -> None:
    """_schedule_datetime parses an ISO format string into a datetime."""
    result = tasks._schedule_datetime("2024-01-15T12:30:45")
    assert isinstance(result, datetime.datetime)
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


def test_schedule_datetime_with_invalid_string_raises_valueerror() -> None:
    """_schedule_datetime raises ValueError for unparseable input
    (datetime.datetime.fromisoformat does not catch exceptions)."""
    with pytest.raises(ValueError):
        tasks._schedule_datetime("not-a-date")


def test_schedule_datetime_with_datetime_passthrough() -> None:
    """_schedule_datetime returns a datetime input unchanged (or coerced)."""
    dt = datetime.datetime(2024, 1, 15, 12, 30, 45)
    result = tasks._schedule_datetime(dt)
    assert isinstance(result, datetime.datetime)
    assert result.year == 2024


# ── _db_schedule_payload ─────────────────────────────────────────────────────


def test_db_schedule_payload_extracts_fields_from_row() -> None:
    """_db_schedule_payload extracts expected fields from a DB row-like object."""
    row = MagicMock()
    row.tenant_id = "t1"
    row.schedule_id = "s1"
    row.goal_payload = {"goal": "test"}
    row.cron = "0 0 * * *"
    row.interval_seconds = 3600
    row.priority = "high"
    row.enabled = True
    row.last_fired_at = datetime.datetime(2024, 1, 1, 0, 0, 0)
    row.next_fire_at = datetime.datetime(2024, 1, 1, 1, 0, 0)

    result = tasks._db_schedule_payload(row)
    assert isinstance(result, dict)
    # Spot-check at least a few fields
    assert "tenant_id" in result or "schedule_id" in result


# ── _schedule_key ────────────────────────────────────────────────────────────


def test_schedule_key_concatenates_tenant_and_schedule_id() -> None:
    """_schedule_key produces a deterministic Redis key."""
    key = tasks._schedule_key("tenant-42", "sched-99")
    assert "tenant-42" in key
    assert "sched-99" in key
