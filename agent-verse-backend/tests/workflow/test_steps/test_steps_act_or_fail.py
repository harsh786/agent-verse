"""sub_workflow / emit_event / wait-on-event must act in real runs — or fail.

Old bug: neither the API nor the worker gave the workflow compiler a runner or
Redis, so in every real run ``sub_workflow`` returned ``{"_mock": True}``,
``emit_event`` published nothing and a wait-on-event returned immediately —
all reported as successful steps. Now each step either does the real thing
(durably, via the run store) or fails with a clear error; only ``is_test_run``
simulates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus
from app.workflow.steps.emit_event_step import EmitEventStepNode, tenant_event_channel
from app.workflow.steps.sub_workflow_step import SubWorkflowStepNode
from app.workflow.steps.wait_step import WaitStepNode

pytestmark = pytest.mark.asyncio


def _state(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "r1", "tenant_id": "t1", "step_outputs": {}, "inputs": {}, "vars": {},
        "is_test_run": False, "mock_overrides": {},
    }
    base.update(kw)
    return base


class _Store:
    """In-memory model of PostgresWorkflowRunStore's durable-wait methods."""

    def __init__(self) -> None:
        self.timer: dict[tuple[str, str], datetime] = {}
        self.waits: dict[tuple[str, str], str] = {}  # (run, step) -> channel
        self.deliveries: dict[tuple[str, str], dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.woken: list[str] = []

    async def get_timer_wait(self, tid: str, rid: str, step: str) -> str | None:
        w = self.timer.get((rid, step))
        return w.isoformat() if w else None

    async def set_timer_wait(self, tid: str, rid: str, step: str, at: datetime) -> None:
        self.timer[(rid, step)] = at

    async def register_event_wait(
        self, tid: str, rid: str, step: str, ch: str, deadline: datetime
    ) -> None:
        self.waits[(rid, step)] = ch
        self.timer[(rid, step)] = deadline

    async def get_event_delivery(self, tid: str, rid: str, step: str) -> dict[str, Any] | None:
        return self.deliveries.get((rid, step))

    async def clear_event_wait(self, tid: str, rid: str, step: str) -> None:
        self.waits.pop((rid, step), None)

    async def deliver_event(self, tid: str, ch: str, payload: dict[str, Any]) -> int:
        hits = [k for k, c in self.waits.items() if c == ch]
        for k in hits:
            self.deliveries[k] = payload
            self.waits.pop(k)
            self.woken.append(k[0])
        return len(hits)

    async def get(self, tid: str, rid: str) -> dict[str, Any] | None:
        return self.runs.get(rid)


# ── sub_workflow ──────────────────────────────────────────────────────────────


def _sub(**services: Any) -> SubWorkflowStepNode:
    step = StepDefinition(
        id="sub", type="sub_workflow", workflow_id="child-wf", workflow_inputs={"x": 1}
    )
    return SubWorkflowStepNode(step, ContextResolver(), **services)


async def test_sub_workflow_without_runner_fails_in_real_run() -> None:
    with pytest.raises(RuntimeError, match="no workflow runner"):
        await _sub().execute(_state())  # type: ignore[arg-type]


async def test_sub_workflow_test_run_is_simulated() -> None:
    out = await _sub().execute(_state(is_test_run=True))  # type: ignore[arg-type]
    assert out["step_outputs"]["sub"]["_mock"] is True


async def test_sub_workflow_durable_dispatches_child_once_and_suspends() -> None:
    store = _Store()
    runner = MagicMock(_run_store=store)
    runner.run = AsyncMock(return_value="child-1")
    store.runs["child-1"] = {"status": "running"}
    node = _sub(workflow_runner=runner, run_store=store, durable_timer_waits=True)

    res = await node.execute(_state())  # type: ignore[arg-type]
    assert res["status"] == WorkflowRunStatus.WAITING_TIMER
    assert res["paused_by"] == "wait_timer:sub"
    kw = runner.run.await_args.kwargs
    assert kw["idempotency_key"] == "sub:r1:sub"  # re-execution -> same child
    assert kw["run_metadata"] == {"parent_run_id": "r1", "parent_step_id": "sub"}
    assert ("r1", "sub") in store.timer  # beat will re-check

    # Child finished -> the woken parent step completes with the child's outputs.
    store.runs["child-1"] = {"status": "complete", "outputs": {"y": 2}}
    res = await node.execute(_state())  # type: ignore[arg-type]
    assert "status" not in res
    assert res["step_outputs"]["sub"] == {
        "run_id": "child-1", "workflow_id": "child-wf", "status": "complete",
        "outputs": {"y": 2},
    }
    assert runner.run.await_args.kwargs["idempotency_key"] == "sub:r1:sub"


async def test_sub_workflow_child_failure_fails_the_step() -> None:
    store = _Store()
    runner = MagicMock(_run_store=store)
    runner.run = AsyncMock(return_value="child-1")
    store.runs["child-1"] = {"status": "failed", "error": "boom"}
    node = _sub(workflow_runner=runner, run_store=store, durable_timer_waits=True)
    with pytest.raises(RuntimeError, match="boom"):
        await node.execute(_state())  # type: ignore[arg-type]


async def test_sub_workflow_runner_without_store_runs_child_inline() -> None:
    runner = MagicMock(_run_store=None)
    runner.run = AsyncMock(return_value="child-1")
    res = await _sub(workflow_runner=runner).execute(_state())  # type: ignore[arg-type]
    assert res["step_outputs"]["sub"]["run_id"] == "child-1"
    assert runner.run.await_args.kwargs["wait_for_completion"] is True


# ── emit_event ────────────────────────────────────────────────────────────────


def _emit(**services: Any) -> EmitEventStepNode:
    step = StepDefinition(
        id="e", type="emit_event", event_channel_out="orders", event_payload={"k": "v"}
    )
    return EmitEventStepNode(step, ContextResolver(), **services)


async def test_emit_without_redis_or_store_fails_in_real_run() -> None:
    with pytest.raises(RuntimeError, match="cannot publish"):
        await _emit().execute(_state())  # type: ignore[arg-type]


async def test_emit_test_run_is_simulated() -> None:
    out = await _emit().execute(_state(is_test_run=True))  # type: ignore[arg-type]
    assert out["step_outputs"]["e"]["simulated"] is True


async def test_emit_publishes_tenant_channel_and_delivers_to_waits() -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    store = _Store()
    store.waits[("waiter", "w")] = "orders"
    out = (await _emit(redis=redis, run_store=store).execute(_state()))["step_outputs"]["e"]
    ch, body = redis.publish.await_args.args
    assert ch == tenant_event_channel("t1", "orders") == "wf_event:t1:orders"
    assert json.loads(body)["k"] == "v"
    assert out["delivered_to_waits"] == 1
    assert store.deliveries[("waiter", "w")]["k"] == "v"


# ── wait-on-event ─────────────────────────────────────────────────────────────


def _wait(duration: str = "1h", **services: Any) -> WaitStepNode:
    step = StepDefinition(id="w", type="wait", event_channel="orders", duration=duration)
    return WaitStepNode(step, ContextResolver(), **services)


async def test_wait_on_event_without_any_backend_fails() -> None:
    with pytest.raises(RuntimeError, match="no event could ever arrive"):
        await _wait().execute(_state())  # type: ignore[arg-type]


async def test_wait_on_event_suspends_until_emitted_then_completes() -> None:
    store = _Store()
    waiter = _wait(run_store=store, durable_timer_waits=True)
    res = await waiter.execute(_state(run_id="waiter"))  # type: ignore[arg-type]
    assert res["status"] == WorkflowRunStatus.WAITING_TIMER
    assert store.waits[("waiter", "w")] == "orders"
    deadline = store.timer[("waiter", "w")]
    assert deadline > datetime.now(UTC) + timedelta(minutes=59)  # 1h timeout

    # Another run of the same tenant emits -> the waiter is woken with the event.
    redis = MagicMock(publish=AsyncMock(return_value=0))
    await _emit(redis=redis, run_store=store).execute(_state(run_id="emitter"))  # type: ignore[arg-type]
    assert store.woken == ["waiter"]

    res = await waiter.execute(_state(run_id="waiter"))  # type: ignore[arg-type]
    out = res["step_outputs"]["w"]
    assert "status" not in res
    assert out["timed_out"] is False and out["event"]["k"] == "v"


async def test_wait_on_event_times_out_at_deadline() -> None:
    store = _Store()
    store.timer[("r1", "w")] = datetime.now(UTC) - timedelta(seconds=1)
    store.waits[("r1", "w")] = "orders"
    res = await _wait(run_store=store, durable_timer_waits=True).execute(_state())  # type: ignore[arg-type]
    assert res["step_outputs"]["w"]["timed_out"] is True
    assert ("r1", "w") not in store.waits


async def test_wait_on_event_redis_only_listens_on_tenant_channel() -> None:
    node = _wait(duration="1s", redis=MagicMock())
    node._await_event = AsyncMock(return_value={"a": 1})  # type: ignore[method-assign]
    out = (await node.execute(_state()))["step_outputs"]["w"]  # type: ignore[arg-type]
    assert node._await_event.await_args.args[0] == "wf_event:t1:orders"
    assert out["event"] == {"a": 1}


# ── wiring ────────────────────────────────────────────────────────────────────


async def test_create_app_compiler_is_given_the_runner() -> None:
    from app.main import create_app

    app = create_app()
    runner = app.state.workflow_runner
    assert runner._compiler.services.get("workflow_runner") is runner


async def test_worker_runner_binds_runner_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workflow.celery_tasks as ct

    monkeypatch.setattr(ct, "_WORKER_RUNNER", None)
    runner = ct._build_worker_runner()
    try:
        services = runner._compiler.services
        assert services.get("workflow_runner") is runner
        assert services.get("redis") is not None
        assert services.get("run_store") is runner._run_store
    finally:
        monkeypatch.setattr(ct, "_WORKER_RUNNER", None)
