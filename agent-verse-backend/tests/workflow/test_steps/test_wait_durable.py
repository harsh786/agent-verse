"""Regression: timer waits report the truth and long waits are durable.

Old bug: ``WaitStepNode`` did ``asyncio.sleep(min(seconds, 300))`` while
reporting ``waited_seconds = seconds`` — a 1h wait "completed" after 5 minutes,
and even that held a Celery worker slot the whole time.

Now: short waits (or when no durable backend is wired) sleep for the FULL
duration and report what actually elapsed; with durable waits enabled a long
wait persists its wake time, suspends the run (``waiting_timer``) and ends the
task, and a beat scan re-dispatches the run once the wake time has passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.state import StepStatus, WorkflowRunStatus


def _state(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "r1",
        "tenant_id": "t1",
        "step_outputs": {},
        "is_test_run": False,
        "mock_overrides": {},
    }
    base.update(kw)
    return base


class _TimerStore:
    def __init__(self) -> None:
        self.waits: dict[tuple[str, str], datetime] = {}

    async def get_timer_wait(self, tenant_id: str, run_id: str, step_id: str) -> str | None:
        w = self.waits.get((run_id, step_id))
        return w.isoformat() if w else None

    async def set_timer_wait(
        self, tenant_id: str, run_id: str, step_id: str, wake_at: datetime
    ) -> None:
        self.waits[(run_id, step_id)] = wake_at


async def test_non_durable_wait_sleeps_full_duration_and_reports_truth() -> None:
    from app.workflow.steps.wait_step import WaitStepNode

    node = WaitStepNode(StepDefinition(id="w", type="wait", duration="10m"), ContextResolver())
    with patch("app.workflow.steps.wait_step.asyncio.sleep", new=AsyncMock()) as slept:
        out = (await node.execute(_state()))["step_outputs"]["w"]
    slept.assert_awaited_once_with(600.0)  # not capped at 300
    assert out["requested_seconds"] == 600.0
    # waited_seconds is MEASURED (sleep is patched here, so ~0) — never the
    # requested figure echoed back.
    assert out["waited_seconds"] < 600.0
    assert out["durable"] is False


async def test_durable_long_wait_suspends_instead_of_sleeping() -> None:
    from app.workflow.steps.wait_step import WaitStepNode

    store = _TimerStore()
    node = WaitStepNode(
        StepDefinition(id="w", type="wait", duration="1h"),
        ContextResolver(),
        run_store=store,
        durable_timer_waits=True,
    )
    before = datetime.now(UTC)
    with patch("app.workflow.steps.wait_step.asyncio.sleep", new=AsyncMock()) as slept:
        result = await node.execute(_state())
    slept.assert_not_awaited()  # no worker slot held
    assert result["status"] == WorkflowRunStatus.WAITING_TIMER
    assert result["paused_by"] == "wait_timer:w"
    wake = store.waits[("r1", "w")]
    assert timedelta(minutes=59) < wake - before <= timedelta(hours=1, seconds=5)


async def test_durable_wait_completes_once_wake_time_passed() -> None:
    from app.workflow.steps.wait_step import WaitStepNode

    store = _TimerStore()
    store.waits[("r1", "w")] = datetime.now(UTC) - timedelta(seconds=1)
    node = WaitStepNode(
        StepDefinition(id="w", type="wait", duration="1h"),
        ContextResolver(),
        run_store=store,
        durable_timer_waits=True,
    )
    result = await node.execute(_state())
    assert "status" not in result
    out = result["step_outputs"]["w"]
    assert out["durable"] is True
    assert out["requested_seconds"] == 3600.0
    assert out["waited_seconds"] >= 3600.0


async def test_durable_wait_woken_early_re_suspends_with_same_wake_time() -> None:
    from app.workflow.steps.wait_step import WaitStepNode

    store = _TimerStore()
    wake = datetime.now(UTC) + timedelta(minutes=30)
    store.waits[("r1", "w")] = wake
    node = WaitStepNode(
        StepDefinition(id="w", type="wait", duration="1h"),
        ContextResolver(),
        run_store=store,
        durable_timer_waits=True,
    )
    result = await node.execute(_state())
    assert result["status"] == WorkflowRunStatus.WAITING_TIMER
    assert store.waits[("r1", "w")] == wake  # not pushed back


# ── Engine level: suspend → beat wake → resume from where it stopped ──────────


class _RunStore(_TimerStore):
    def __init__(self, definition: dict[str, Any]) -> None:
        super().__init__()
        self.definition = definition
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: dict[tuple[str, str], dict[str, Any]] = {}
        self.executed: list[str] = []

    async def create(self, *, run_id: str, workflow_id: str, tenant_id: str, **kw: Any) -> str:
        self.runs[run_id] = {"run_id": run_id, "workflow_id": workflow_id, "status": "pending"}
        return run_id

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        return self.runs[run_id]["status"]

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.runs[run_id]["status"] = str(getattr(status, "value", status))
        return True

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.definition

    async def record_step_start(self, *, run_id: str, step_id: str, **kw: Any) -> str:
        self.executed.append(step_id)
        return "x"

    async def record_step_finish(
        self, *, run_id: str, step_id: str, status: Any, output: Any = None, **kw: Any
    ) -> bool:
        self.steps[(run_id, step_id)] = {
            "status": str(getattr(status, "value", status)),
            "output": output,
        }
        return True

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        return self.steps.get((run_id, step_id))


async def test_engine_suspends_on_long_wait_then_resumes_after_wake() -> None:
    from app.workflow.compiler import WorkflowCompiler
    from app.workflow.runner import WorkflowRunner

    wf = WorkflowDefinition(
        name="timer",
        steps=[
            StepDefinition(id="a", type="transform", input={"v": 1}),
            StepDefinition(id="w", type="wait", duration="2h", depends_on=["a"]),
            StepDefinition(id="b", type="transform", input={"v": 2}, depends_on=["w"]),
        ],
    )
    store = _RunStore(wf.model_dump())
    compiler = WorkflowCompiler(ContextResolver(), run_store=store, durable_timer_waits=True)
    runner = WorkflowRunner(compiler=compiler, run_store=store)

    run_id = await runner.run("wf-1", "t-1", {}, wait_for_completion=True)
    assert store.runs[run_id]["status"] == WorkflowRunStatus.WAITING_TIMER.value
    assert "b" not in store.executed  # downstream of the wait did not run
    assert store.steps[(run_id, "w")]["status"] != StepStatus.COMPLETE.value

    # Beat claims the due run (wake time passed) and the worker re-executes it.
    store.waits[(run_id, "w")] = datetime.now(UTC) - timedelta(seconds=1)
    store.runs[run_id]["status"] = "pending"
    store.executed.clear()
    await runner.execute_fresh(run_id, "wf-1", "t-1")

    assert store.runs[run_id]["status"] == WorkflowRunStatus.COMPLETE.value
    assert "a" not in store.executed  # completed before the wait: not redone
    assert store.executed == ["w", "b"]


async def test_beat_wake_dispatches_claimed_runs_and_releases_on_failure() -> None:
    from app.workflow import celery_tasks

    run_store = MagicMock()
    run_store.claim_due_timer_waits = AsyncMock(
        return_value=[
            {"run_id": "r1", "tenant_id": "t1", "workflow_id": "w1", "is_test_run": False},
            {"run_id": "r2", "tenant_id": "t2", "workflow_id": "w2", "is_test_run": False},
        ]
    )
    run_store.release_timer_claim = AsyncMock()
    runner = MagicMock()
    runner._run_store = run_store
    runner._get_plan_tier = AsyncMock(return_value="starter")

    calls: list[Any] = []

    def _apply_async(**kw: Any) -> None:
        calls.append(kw)
        if kw["args"][0] == "r2":
            raise RuntimeError("broker down")

    with (
        patch.object(celery_tasks, "_get_runner", return_value=runner),
        patch.object(celery_tasks.execute_workflow_run, "apply_async", side_effect=_apply_async),
    ):
        out = await celery_tasks.wake_due_timer_waits_async()

    assert out == {"claimed": 2, "dispatched": 1}
    assert calls[0]["args"] == ["r1", "w1", "t1"]
    assert calls[0]["queue"] == "workflows.starter"
    run_store.release_timer_claim.assert_awaited_once_with("t2", "r2")


def test_wake_beat_is_scheduled() -> None:
    from app.scaling.celery_app import celery_app

    entries = {v["task"] for v in celery_app.conf.beat_schedule.values()}
    assert "workflow.wake_due_timer_waits" in entries


@pytest.mark.parametrize("dur,secs", [("45s", 45.0), ("2m", 120.0), ("1h", 3600.0), ("7", 7.0)])
def test_parse_seconds(dur: str, secs: float) -> None:
    from app.workflow.steps.wait_step import WaitStepNode

    assert WaitStepNode._parse_seconds(dur) == secs
