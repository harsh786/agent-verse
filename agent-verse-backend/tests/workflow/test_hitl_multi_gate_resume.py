"""WF-01: resuming a two-gate workflow on the cross-process (Celery) path.

``pre -> gate1 -> mid -> gate2 -> after``. Each approval is resumed through
``WorkflowRunner.execute_resume_fresh`` — the path a Celery worker takes — which
rebuilds the run from the persisted run record + step results.

Old bug: an approval step's decided result was persisted with the *run* status
the node returned (``running``), not ``complete``, so the engine's
"skip already-completed steps" check never matched it. Approving gate2 re-entered
gate1 as a brand-new suspend, created a second gate1 approval and parked the run
at gate1 again — forever. A step whose output is not a JSON object (e.g. an HTTP
step returning a JSON array) was persisted with no output and so re-executed —
replaying its side effect — on every resume.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.runner import WorkflowRunner

_T = "t-1"
_RUN = "run-1"
_WF = "wf-1"


class _HistoryRunStore:
    """Run store with Postgres-like step history: every start appends a row,
    finish updates the latest row, reads return the latest row."""

    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.runs: dict[str, dict[str, Any]] = {
            _RUN: {"run_id": _RUN, "workflow_id": _WF, "tenant_id": _T, "status": "pending"}
        }
        self.rows: list[dict[str, Any]] = []

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        return str(self.runs[run_id]["status"])

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.runs[run_id]["status"] = str(getattr(status, "value", status))
        return True

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return _WF

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.definition

    async def record_step_start(self, *, run_id: str, step_id: str, **kw: Any) -> str:
        self.rows.append({"run_id": run_id, "step_id": step_id, "status": "running"})
        return str(len(self.rows))

    async def record_step_finish(
        self, *, run_id: str, step_id: str, status: Any, output: Any = None, **kw: Any
    ) -> bool:
        for row in reversed(self.rows):
            if row["run_id"] == run_id and row["step_id"] == step_id:
                row["status"] = str(getattr(status, "value", status))
                row["output"] = output
                return True
        return False

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        for row in reversed(self.rows):
            if row["run_id"] == run_id and row["step_id"] == step_id:
                return dict(row)
        return None

    def executions(self, step_id: str) -> int:
        return sum(1 for r in self.rows if r["step_id"] == step_id)


class _Gateway:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def create_workflow_approval(self, *, step_id: str, **kw: Any) -> str:
        self.created.append(step_id)
        return f"req-{len(self.created)}"


class _Redis:
    """Counts emit_event publishes — the side effect of step ``pre``."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append(channel)
        return 1


def _gate(step_id: str, dep: str) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        type="hitl",
        depends_on=[dep],
        actions=[{"id": "approve"}, {"id": "reject"}],
    )


def _definition() -> dict[str, Any]:
    return WorkflowDefinition(
        name="two-gates",
        id=_WF,
        steps=[
            StepDefinition(
                id="pre",
                type="emit_event",
                event_channel_out="side-effect",
                event_payload={"n": 1},
            ),
            _gate("gate1", "pre"),
            StepDefinition(id="mid", type="transform", input={"m": 1}, depends_on=["gate1"]),
            _gate("gate2", "mid"),
            StepDefinition(id="after", type="transform", input={"a": 1}, depends_on=["gate2"]),
        ],
    ).to_json()


def _runner(store: _HistoryRunStore, gateway: _Gateway, redis: _Redis) -> WorkflowRunner:
    compiler = WorkflowCompiler(
        ContextResolver(), run_store=store, hitl_workflow_gateway=gateway, redis=redis
    )
    return WorkflowRunner(compiler=compiler, run_store=store)


async def _approve(runner: WorkflowRunner, step_id: str) -> None:
    await runner.execute_resume_fresh(
        _RUN, _WF, _T, step_id=step_id, action="approve", actor_id="alice"
    )


@pytest.mark.asyncio
async def test_two_gate_workflow_resumes_from_persisted_results() -> None:
    store, gateway, redis = _HistoryRunStore(_definition()), _Gateway(), _Redis()
    runner = _runner(store, gateway, redis)

    await runner.execute_fresh(_RUN, _WF, _T)
    assert store.runs[_RUN]["status"] == "waiting_hitl"
    assert gateway.created == ["gate1"]

    await _approve(runner, "gate1")
    assert store.runs[_RUN]["status"] == "waiting_hitl"
    assert gateway.created == ["gate1", "gate2"], "gate1 approval must not be recreated"
    assert (await store.get_step_result(_T, _RUN, "gate1") or {})["status"] == "complete"

    await _approve(runner, "gate2")
    assert store.runs[_RUN]["status"] == "complete"
    # No new approvals after the last decision, and gate1 was never re-suspended.
    assert gateway.created == ["gate1", "gate2"]
    # pre's side effect happened exactly once across the fresh run and both resumes.
    assert len(redis.published) == 1
    assert store.executions("pre") == 1
    assert store.executions("mid") == 1
    assert store.executions("after") == 1


@pytest.mark.asyncio
async def test_non_object_step_output_is_not_replayed_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed step whose output is a JSON array (an HTTP API returning a
    list) must be skipped on resume and its output restored for later steps."""
    from app.workflow.steps.emit_event_step import EmitEventStepNode

    calls: list[int] = []

    async def _list_output(self: Any, state: Any) -> dict[str, Any]:
        calls.append(1)
        return {"step_outputs": {self.step.id: [1, 2, 3]}}

    monkeypatch.setattr(EmitEventStepNode, "execute", _list_output)
    store, gateway, redis = _HistoryRunStore(_definition()), _Gateway(), _Redis()
    runner = _runner(store, gateway, redis)

    await runner.execute_fresh(_RUN, _WF, _T)
    await _approve(runner, "gate1")
    await _approve(runner, "gate2")

    assert store.runs[_RUN]["status"] == "complete"
    assert len(calls) == 1
    assert (await store.get_step_result(_T, _RUN, "pre") or {})["output"] == [1, 2, 3]
