"""Fast-tier coverage for the workflow Celery tasks (execution, HITL resume,
periodic maintenance, schedule-fire scan).

``tests/workflow/test_worker_runner.py`` already covers ``_get_runner`` /
``_build_worker_runner``. This file covers the task bodies themselves —
``execute_workflow_run``, the three maintenance tasks, ``_run_async``'s two
event-loop branches, and ``fire_due_workflow_schedules``' scan/dedup/grace-
window logic — all with the DB/Celery/Redis pieces mocked out (no live infra),
per the project's fast-tier convention (call the task function directly /
``.run()``, never a real worker).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.workflow.celery_tasks as ct


# ── _run_async ──────────────────────────────────────────────────────────────────


def test_run_async_no_running_loop_uses_run_until_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``get_event_loop()`` returns a loop that isn't running, the coro is
    driven via ``loop.run_until_complete`` directly."""

    class _FakeLoop:
        def is_running(self) -> bool:
            return False

        def run_until_complete(self, coro: object) -> object:
            return asyncio.run(coro)  # actually drive it so it isn't "never awaited"

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop())

    async def _coro() -> int:
        return 99

    assert ct._run_async(_coro()) == 99


def test_run_async_get_event_loop_raises_runtime_error_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise RuntimeError("no current event loop")

    monkeypatch.setattr(asyncio, "get_event_loop", _raise)

    async def _coro() -> int:
        return 7

    assert ct._run_async(_coro()) == 7


@pytest.mark.asyncio
async def test_run_async_running_loop_uses_thread_pool() -> None:
    """Inside a running loop (as in an async test), ``_run_async`` must hop to a
    worker thread rather than deadlock on the current loop."""

    async def _coro() -> str:
        return "from-thread"

    result = ct._run_async(_coro())
    assert result == "from-thread"


# ── execute_workflow_run ─────────────────────────────────────────────────────


def test_execute_workflow_run_returns_early_when_runner_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: None)
    # Should not raise even though nothing else is wired up.
    ct.execute_workflow_run.run(run_id="r1", workflow_id="wf1", tenant_id="t1")


def test_execute_workflow_run_fresh_dispatches_execute_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AsyncMock()
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    ct.execute_workflow_run.run(
        run_id="r1",
        workflow_id="wf1",
        tenant_id="t1",
        is_test_run=True,
        mock_overrides={"step-1": {"ok": True}},
    )

    runner.execute_fresh.assert_awaited_once_with(
        "r1", "wf1", "t1", is_test_run=True, mock_overrides={"step-1": {"ok": True}}
    )


def test_execute_workflow_run_cross_process_hitl_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = AsyncMock()
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    decision = {
        "step_id": "s1",
        "action": "approve",
        "actor_id": "user-1",
        "note": "looks good",
        "form_data": {"amount": 10},
    }
    ct.execute_workflow_run.run(
        run_id="r1", workflow_id="wf1", tenant_id="t1", resume=True, hitl_decision=decision
    )

    runner.execute_resume_fresh.assert_awaited_once_with(
        "r1",
        "wf1",
        "t1",
        step_id="s1",
        action="approve",
        actor_id="user-1",
        note="looks good",
        form_data={"amount": 10},
    )


def test_execute_workflow_run_legacy_same_process_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    compiled = AsyncMock()
    current_state = SimpleNamespace(values={"foo": "bar"})
    compiled.aget_state = AsyncMock(return_value=current_state)
    compiled.ainvoke = AsyncMock(return_value=None)

    runner = MagicMock()
    runner._load_definition = AsyncMock(return_value=SimpleNamespace(name="wf"))
    runner._compiler = MagicMock()
    runner._compiler.compile = MagicMock(return_value=compiled)
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    ct.execute_workflow_run.run(run_id="r1", workflow_id="wf1", tenant_id="t1", resume=True)

    runner._load_definition.assert_awaited_once_with("wf1", "t1")
    runner._compiler.compile.assert_called_once()
    compiled.aget_state.assert_awaited_once_with({"configurable": {"thread_id": "r1"}})
    compiled.ainvoke.assert_awaited_once_with(
        {"foo": "bar"}, {"configurable": {"thread_id": "r1"}}
    )


def test_execute_workflow_run_legacy_resume_no_state_values_skips_ainvoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = AsyncMock()
    compiled.aget_state = AsyncMock(return_value=None)  # no `.values` attribute
    compiled.ainvoke = AsyncMock()

    runner = MagicMock()
    runner._load_definition = AsyncMock(return_value=SimpleNamespace(name="wf"))
    runner._compiler = MagicMock()
    runner._compiler.compile = MagicMock(return_value=compiled)
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    ct.execute_workflow_run.run(run_id="r1", workflow_id="wf1", tenant_id="t1", resume=True)

    compiled.ainvoke.assert_not_awaited()


def test_execute_workflow_run_reraises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = AsyncMock()
    runner.execute_fresh.side_effect = RuntimeError("boom")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    with pytest.raises(RuntimeError, match="boom"):
        ct.execute_workflow_run.run(run_id="r1", workflow_id="wf1", tenant_id="t1")


# ── check_hitl_escalations ────────────────────────────────────────────────────


def test_check_hitl_escalations_invokes_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app as fastapi_app

    gateway = AsyncMock()
    monkeypatch.setattr(fastapi_app.state, "hitl_workflow_gateway", gateway, raising=False)

    ct.check_hitl_escalations.run()

    gateway.check_and_escalate_overdue.assert_awaited_once()


def test_check_hitl_escalations_no_gateway_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app as fastapi_app

    monkeypatch.setattr(fastapi_app.state, "hitl_workflow_gateway", None, raising=False)
    ct.check_hitl_escalations.run()  # must not raise


def test_check_hitl_escalations_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app as fastapi_app

    gateway = AsyncMock()
    gateway.check_and_escalate_overdue.side_effect = RuntimeError("db down")
    monkeypatch.setattr(fastapi_app.state, "hitl_workflow_gateway", gateway, raising=False)

    ct.check_hitl_escalations.run()  # swallowed, not re-raised


# ── retry_dead_letter_webhooks ────────────────────────────────────────────────


def test_retry_dead_letter_webhooks_retries_each_event(monkeypatch: pytest.MonkeyPatch) -> None:
    run_store = AsyncMock()
    run_store.get_retryable_webhooks.return_value = [
        {"workflow_id": "wf1", "tenant_id": "t1", "payload": {"a": 1}},
        {"workflow_id": "wf2", "tenant_id": "t2", "payload": {"b": 2}},
    ]
    runner = MagicMock()
    runner._run_store = run_store
    runner.run = AsyncMock()
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    ct.retry_dead_letter_webhooks.run()

    run_store.get_retryable_webhooks.assert_awaited_once_with(max_attempts=3)
    assert runner.run.await_count == 2
    runner.run.assert_any_await(
        workflow_id="wf1", tenant_id="t1", inputs={"a": 1}, trigger_type="webhook"
    )


def test_retry_dead_letter_webhooks_no_runner_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: None)
    ct.retry_dead_letter_webhooks.run()  # must not raise


def test_retry_dead_letter_webhooks_no_run_store_attr_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SimpleNamespace()  # no `_run_store`
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    ct.retry_dead_letter_webhooks.run()


def test_retry_dead_letter_webhooks_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    run_store = AsyncMock()
    run_store.get_retryable_webhooks.side_effect = RuntimeError("db down")
    runner = MagicMock()
    runner._run_store = run_store
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    ct.retry_dead_letter_webhooks.run()  # swallowed


# ── cleanup_expired_runs ──────────────────────────────────────────────────────


def test_cleanup_expired_runs_deletes_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    run_store = AsyncMock()
    run_store.delete_expired_runs.return_value = 5
    runner = MagicMock()
    runner._run_store = run_store
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    ct.cleanup_expired_runs.run()

    run_store.delete_expired_runs.assert_awaited_once()


def test_cleanup_expired_runs_no_runner_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: None)
    ct.cleanup_expired_runs.run()


def test_cleanup_expired_runs_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    run_store = AsyncMock()
    run_store.delete_expired_runs.side_effect = RuntimeError("db down")
    runner = MagicMock()
    runner._run_store = run_store
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    ct.cleanup_expired_runs.run()


# ── _sched_redis ──────────────────────────────────────────────────────────────


def test_sched_redis_returns_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = MagicMock()
    monkeypatch.setattr("app.scaling.tasks._get_sync_redis", lambda: fake_redis)
    assert ct._sched_redis() is fake_redis


def test_sched_redis_returns_none_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> None:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.scaling.tasks._get_sync_redis", _raise)
    assert ct._sched_redis() is None


# ── _cron_bounds (a couple of extra branches beyond the existing suite) ──────


def test_cron_bounds_respects_timezone() -> None:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    bounds = ct._cron_bounds("0 9 * * *", now, "Asia/Kolkata")
    assert bounds is not None


def test_cron_bounds_bad_timezone_falls_back_to_utc() -> None:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    bounds = ct._cron_bounds("* * * * *", now, "Not/ARealZone")
    assert bounds is not None


# ── fire_due_workflow_schedules ───────────────────────────────────────────────


class _FakeExecResult:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.executed = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> _FakeExecResult:
        self.executed += 1
        # Call #1 is system_session's "SET LOCAL row_security = off"; call #2 is
        # the real workflows scan query.
        if self.executed == 1:
            return _FakeExecResult()
        return _FakeExecResult(self._rows)

    def begin(self) -> _FakeSession:
        return self


def _patch_db_rows(monkeypatch: pytest.MonkeyPatch, rows: list[tuple]) -> None:
    session = _FakeSession(rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: (lambda: session))


@pytest.mark.asyncio
async def test_fire_due_schedules_no_runner_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: None)
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 0, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_no_published_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    _patch_db_rows(monkeypatch, [])
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 0, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_skips_non_schedule_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    definition = {"trigger": {"type": "webhook"}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 0, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_skips_empty_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    definition = {"trigger": {"type": "schedule"}}  # schedule_cron -> ("", "UTC")
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 0, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_invalid_cron_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "not-a-cron"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])
    result = ct.fire_due_workflow_schedules.run()
    # scanned increments (cron string is non-empty) but nothing fires.
    assert result == {"scanned": 1, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_cron_bounds_exception_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("bad tz")

    monkeypatch.setattr(ct, "_cron_bounds", _raise)
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 1, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_outside_grace_window_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    now = datetime.now(UTC)
    far_prev = now - timedelta(seconds=ct._SCHEDULE_GRACE_SECONDS + 1000)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (far_prev, now + timedelta(minutes=1)))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 1, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_fires_run_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value="run-123")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)
    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 1, "fired": 1}
    runner.run.assert_awaited_once()
    kwargs = runner.run.await_args.kwargs
    assert kwargs["workflow_id"] == "wf-1"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["trigger_type"] == "schedule"


@pytest.mark.asyncio
async def test_fire_due_schedules_redis_dedup_already_claimed_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value="run-123")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    fake_redis = MagicMock()
    fake_redis.set.return_value = False  # already claimed by another scan/replica
    monkeypatch.setattr(ct, "_sched_redis", lambda: fake_redis)

    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 1, "fired": 0}
    runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_fire_due_schedules_redis_dedup_claims_and_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value="run-123")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    monkeypatch.setattr(ct, "_sched_redis", lambda: fake_redis)

    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 1, "fired": 1}
    fake_redis.set.assert_called_once()
    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_fire_due_schedules_redis_set_raises_is_caught_and_fires_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The redis SETNX failing (e.g. connection error) must not block firing —
    it's a best-effort dedup, not a correctness requirement."""
    runner = MagicMock()
    runner.run = AsyncMock(return_value="run-123")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)

    fake_redis = MagicMock()
    fake_redis.set.side_effect = RuntimeError("connection reset")
    monkeypatch.setattr(ct, "_sched_redis", lambda: fake_redis)

    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 1, "fired": 1}
    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_fire_due_schedules_runner_run_exception_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=RuntimeError("dispatch failed"))
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)

    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))
    definition = {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}
    _patch_db_rows(monkeypatch, [("wf-1", "t1", definition)])

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 1, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_top_level_exception_returns_zeroed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct, "_get_runner", lambda: MagicMock())

    def _raise() -> None:
        raise RuntimeError("db factory unavailable")

    monkeypatch.setattr("app.db.session.get_session_factory", _raise)
    result = ct.fire_due_workflow_schedules.run()
    assert result == {"scanned": 0, "fired": 0}


@pytest.mark.asyncio
async def test_fire_due_schedules_multiple_rows_only_counts_schedule_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value="run-1")
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    monkeypatch.setattr(ct, "_sched_redis", lambda: None)

    now = datetime.now(UTC)
    prev = now - timedelta(seconds=5)
    nxt = now + timedelta(minutes=1)
    monkeypatch.setattr(ct, "_cron_bounds", lambda *a, **k: (prev, nxt))

    rows = [
        ("wf-1", "t1", {"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}}),
        ("wf-2", "t2", {"trigger": {"type": "webhook"}}),
        ("wf-3", "t3", {"triggers": [{"type": "schedule", "schedule": {"cron": "* * * * *"}}]}),
    ]
    _patch_db_rows(monkeypatch, rows)

    result = ct.fire_due_workflow_schedules.run()

    assert result == {"scanned": 2, "fired": 2}
    assert runner.run.await_count == 2
