"""Coverage gaps for app/scaling/tasks.py.

Targets missing lines:
  96-97   — _setup_worker_checkpointer body
  248-250 — _record_goal_duration_metric exception path
  337-340 — _run_with_signals cancel path
  345-346 — _run_with_signals pause path
  364-365 — _run_with_signals resume log
  393-394 — _WorkerMCPAgentRunner with system_prompt
  401-402 — _WorkerMCPAgentRunner mcp_client injection
  474     — run_goal_dlq missing payload early return
  485-486,489 — _update_goal_dlq DB exception
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _setup_worker_checkpointer ────────────────────────────────────────────────

def test_setup_worker_checkpointer_runs():
    """Signal handler body executes without error."""
    from app.scaling.tasks import _setup_worker_checkpointer
    # Call directly — the signal handler just logs
    _setup_worker_checkpointer()


# ── _SyncGoalLock ─────────────────────────────────────────────────────────────

def test_sync_goal_lock_acquire_release():
    from app.scaling.tasks import _SyncGoalLock

    mock_redis = MagicMock()
    mock_redis.set.return_value = True  # acquired
    mock_redis.eval.return_value = 1    # released

    lock = _SyncGoalLock(mock_redis, lock_value="worker-123")
    assert lock.acquire("goal-abc") is True
    lock.release("goal-abc")  # should not raise
    mock_redis.eval.assert_called_once()


def test_sync_goal_lock_acquire_fails():
    from app.scaling.tasks import _SyncGoalLock

    mock_redis = MagicMock()
    mock_redis.set.return_value = None  # not acquired (another worker holds lock)

    lock = _SyncGoalLock(mock_redis, lock_value="worker-456")
    assert lock.acquire("goal-abc") is False


def test_sync_goal_lock_release_suppresses_errors():
    from app.scaling.tasks import _SyncGoalLock

    mock_redis = MagicMock()
    mock_redis.eval.side_effect = Exception("redis down")

    lock = _SyncGoalLock(mock_redis, lock_value="worker-789")
    lock.release("goal-abc")  # should not raise — contextlib.suppress


# ── _record_goal_duration_metric exception path ───────────────────────────────

def test_record_goal_duration_metric_exception_path():
    """Exception in observability import is swallowed and logged."""
    from app.scaling.tasks import _record_goal_duration_metric
    import time

    with patch("app.scaling.tasks.logger") as mock_logger:
        with patch(
            "app.observability.metrics.record_goal_duration",
            side_effect=RuntimeError("otel down"),
        ):
            # Should not raise
            _record_goal_duration_metric(
                "completed",
                started_monotonic=time.monotonic() - 1.0,
                priority="normal",
            )
    mock_logger.warning.assert_called()


# ── _run_with_signals: cancel path ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_with_signals_cancel():
    """is_cancelled_sync=True → GoalCancelledError raised."""
    from app.scaling.tasks import _run_with_signals
    from app.reliability.goal_lifecycle import GoalCancelledError

    async def _never_ends(**kwargs):
        # Simulate a long-running task that never finishes on its own
        await asyncio.Event().wait()

    mock_runner = MagicMock()
    mock_runner.run = _never_ends
    mock_tenant = MagicMock()
    mock_sync_r = MagicMock()

    with (
        patch("app.scaling.tasks._get_sync_redis", return_value=mock_sync_r),
        patch("app.reliability.goal_lifecycle.is_cancelled_sync", return_value=True),
        patch("app.reliability.goal_lifecycle.is_paused_sync", return_value=False),
        # Make asyncio.sleep instant so the while-loop check runs immediately
        patch("app.scaling.tasks.asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock(return_value=None)
        # asyncio.create_task and asyncio.CancelledError must still work
        mock_asyncio.create_task = asyncio.create_task
        mock_asyncio.CancelledError = asyncio.CancelledError
        mock_asyncio.Event = asyncio.Event

        with pytest.raises(GoalCancelledError):
            await asyncio.wait_for(
                _run_with_signals(
                    mock_runner,
                    goal="test goal",
                    tenant_ctx=mock_tenant,
                    event_callback=None,
                    goal_id="goal-cancel-test",
                ),
                timeout=5.0,
            )


# ── _run_with_signals: pause then resume ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_with_signals_pause_resume():
    """is_paused_sync=True then False → goal resumes and completes."""
    from app.scaling.tasks import _run_with_signals

    async def _run_impl(**kwargs):
        return {"status": "completed"}

    mock_runner = MagicMock()
    mock_runner.run = _run_impl
    mock_tenant = MagicMock()
    mock_sync_r = MagicMock()

    # Use function-based side_effects: StopIteration from exhausted iterators
    # inside a coroutine becomes RuntimeError in Python 3.7+
    paused_sequence = [True, False, False, False, False]
    cancelled_sequence = [False] * 20
    paused_idx = [0]
    cancelled_idx = [0]

    def _paused(*a, **kw):
        i = paused_idx[0]
        paused_idx[0] += 1
        return paused_sequence[i] if i < len(paused_sequence) else False

    def _cancelled(*a, **kw):
        i = cancelled_idx[0]
        cancelled_idx[0] += 1
        return cancelled_sequence[i] if i < len(cancelled_sequence) else False

    with (
        patch("app.scaling.tasks._get_sync_redis", return_value=mock_sync_r),
        patch("app.reliability.goal_lifecycle.is_paused_sync", side_effect=_paused),
        patch("app.reliability.goal_lifecycle.is_cancelled_sync", side_effect=_cancelled),
        patch("app.scaling.tasks.asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock(return_value=None)
        mock_asyncio.create_task = asyncio.create_task
        mock_asyncio.CancelledError = asyncio.CancelledError
        mock_asyncio.Event = asyncio.Event

        result = await asyncio.wait_for(
            _run_with_signals(
                mock_runner,
                goal="test goal",
                tenant_ctx=mock_tenant,
                event_callback=None,
                goal_id="goal-pause-test",
            ),
            timeout=5.0,
        )

    assert result == {"status": "completed"}


# ── _WorkerMCPAgentRunner: system_prompt injection ───────────────────────────

@pytest.mark.asyncio
async def test_worker_mcp_runner_system_prompt_injected():
    """system_prompt is injected into initial_context."""
    from app.scaling.tasks import _WorkerMCPAgentRunner

    received_context: dict = {}

    async def _capture_run(*, goal, tenant_ctx, initial_context=None, **kw):
        received_context.update(initial_context or {})
        return "done"

    inner_runner = MagicMock()
    inner_runner.run = _capture_run

    async def _no_op_factory():
        return None, None, None

    runner = _WorkerMCPAgentRunner(
        inner_runner, _no_op_factory, system_prompt="You are a helpful agent."
    )
    result = await runner.run(
        goal="do something", tenant_ctx=MagicMock(), goal_id="g1"
    )
    assert result == "done"
    assert received_context.get("system_prompt") == "You are a helpful agent."


@pytest.mark.asyncio
async def test_worker_mcp_runner_no_system_prompt():
    """No system_prompt → context not polluted."""
    from app.scaling.tasks import _WorkerMCPAgentRunner

    received_context: dict = {}

    async def _capture_run(*, goal, tenant_ctx, initial_context=None, **kw):
        received_context.update(initial_context or {})
        return "done"

    inner_runner = MagicMock()
    inner_runner.run = _capture_run

    async def _no_op_factory():
        return None, None, None

    runner = _WorkerMCPAgentRunner(inner_runner, _no_op_factory, system_prompt="")
    await runner.run(goal="do something", tenant_ctx=MagicMock(), goal_id="g2")
    assert "system_prompt" not in received_context


@pytest.mark.asyncio
async def test_worker_mcp_runner_mcp_client_injected():
    """mcp_client from factory is set on the inner runner."""
    from app.scaling.tasks import _WorkerMCPAgentRunner

    inner_runner = MagicMock()
    inner_runner.run = AsyncMock(return_value="done")

    mock_mcp_client = MagicMock()

    async def _factory_with_mcp():
        return None, mock_mcp_client, None

    runner = _WorkerMCPAgentRunner(inner_runner, _factory_with_mcp)
    await runner.run(goal="test", tenant_ctx=MagicMock(), goal_id="g3")

    # _mcp_client should be set on inner_runner
    assert getattr(inner_runner, "_mcp_client", None) is mock_mcp_client


@pytest.mark.asyncio
async def test_worker_mcp_runner_tool_context_injected():
    """tool_context from factory is merged into context."""
    from app.scaling.tasks import _WorkerMCPAgentRunner

    received_context: dict = {}

    async def _capture_run(*, goal, tenant_ctx, initial_context=None, **kw):
        received_context.update(initial_context or {})
        return "done"

    inner_runner = MagicMock()
    inner_runner.run = _capture_run

    mock_tool_ctx = MagicMock()
    mock_tool_ctx.to_prompt_block.return_value = "Tool: browser\nTool: search"

    async def _factory_with_tools():
        return None, None, mock_tool_ctx

    runner = _WorkerMCPAgentRunner(inner_runner, _factory_with_tools)
    await runner.run(goal="test", tenant_ctx=MagicMock(), goal_id="g4")
    assert "tool_prompt" in received_context
    assert "tool_context" in received_context


# ── run_goal_dlq: missing payload early return ────────────────────────────────

def test_run_goal_dlq_missing_payload_returns_skipped():
    """DLQ handler returns skipped when called without payload."""
    from app.scaling.tasks import run_goal_dlq

    # Use .run() — Celery's way to call a task directly without a broker
    result = run_goal_dlq.run(goal_id="", tenant_id="", reason="stale beat")
    assert result["status"] == "skipped"
    assert result["reason"] == "missing_dlq_payload"


def test_run_goal_dlq_with_payload():
    """DLQ handler with valid payload marks goal dead-lettered."""
    from app.scaling.tasks import run_goal_dlq

    with patch("app.scaling.tasks._run_async") as mock_run_async:
        mock_run_async.return_value = None
        result = run_goal_dlq.run(
            goal_id="goal-dead",
            tenant_id="tenant-1",
            reason="max retries exceeded",
        )
    assert result["status"] == "dead_lettered"
    assert result["goal_id"] == "goal-dead"


# ── _decrement_after_completion exception suppression ────────────────────────

@pytest.mark.asyncio
async def test_decrement_after_completion_handles_redis_error():
    """Redis errors in decrement are swallowed (non-critical path)."""
    from app.scaling.tasks import _decrement_after_completion

    with patch("redis.asyncio.from_url", side_effect=Exception("connection refused")):
        # Should not raise
        await _decrement_after_completion("tenant-1", "redis://localhost:9999")


# ── _monotonic ────────────────────────────────────────────────────────────────

def test_monotonic_returns_float():
    from app.scaling.tasks import _monotonic
    t = _monotonic()
    assert isinstance(t, float)
    assert t > 0


# ── _scheduled_goal_id ────────────────────────────────────────────────────────

def test_scheduled_goal_id_is_deterministic():
    from app.scaling.tasks import _scheduled_goal_id
    id1 = _scheduled_goal_id("sched:key", fire_instance_id="2026-01-01T00:00:00")
    id2 = _scheduled_goal_id("sched:key", fire_instance_id="2026-01-01T00:00:00")
    assert id1 == id2
    assert id1.startswith("sched_")


def test_scheduled_goal_id_unique_per_instance():
    from app.scaling.tasks import _scheduled_goal_id
    id1 = _scheduled_goal_id("key", fire_instance_id="t1")
    id2 = _scheduled_goal_id("key", fire_instance_id="t2")
    assert id1 != id2


# ── _strip_secret_redis_schedule_fields ──────────────────────────────────────

def test_strip_secrets():
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    sched = {
        "name": "my_sched",
        "api_key": "secret-123",
        "token": "tok-xyz",
        "webhook_token": "wh-abc",
        "interval": 60,
    }
    result = _strip_secret_redis_schedule_fields(sched)
    assert "api_key" not in result
    assert "token" not in result
    assert "webhook_token" not in result
    assert result["name"] == "my_sched"
    assert result["interval"] == 60
