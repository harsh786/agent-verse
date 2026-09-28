"""Regression: a goal re-delivered after a worker crash resumes, not restarts.

``AgentGraph.run`` looked for an ``agent_state`` key in the checkpoint payload
that ``_write_checkpoint`` never stored, and even on a hit it replaced the whole
graph input (dropping ``goal``/``tenant_ctx``). Every re-delivered goal
therefore replanned and re-ran every tool — including side-effecting ones that
had already run before the crash.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.checkpoint_resume import checkpoint_payload, restore_from_checkpoint
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
PLAN = '{"steps": ["file the ticket", "email the customer", "close the loop"]}'


class _CountingProvider(FakeProvider):
    @property
    def prompts(self) -> list[str]:
        return [str(r.messages[-1].content) for r in self.call_history]


def _graph(planner: FakeProvider, executor: FakeProvider) -> AgentGraph:
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    return AgentGraph(planner=planner, executor=executor, verifier=verifier, max_iterations=1)


async def test_rerun_after_crash_skips_completed_steps_and_does_not_replan() -> None:
    store: list[dict[str, Any]] = []

    async def _record(goal_id: str, step_index: int, state: Any, tenant_ctx: Any) -> None:
        # Round-trip through JSON: the payload lives in a JSONB column.
        store.append(json.loads(json.dumps(checkpoint_payload(state, step_index))))

    first_executor = _CountingProvider(responses=["ticket OPS-1 filed", "email sent", "closed"])
    first = _graph(_CountingProvider(responses=[PLAN]), first_executor)
    first._write_checkpoint = _record  # type: ignore[method-assign]
    await first.run(goal="handle the complaint", tenant_ctx=CTX, goal_id="g1")
    assert len(first_executor.prompts) == 3

    # The worker "crashed" right after the second step's checkpoint was written.
    crash_point = next(p for p in store if len(p["completed"]) == 2)

    async def _load(goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return crash_point

    planner = _CountingProvider(responses=[PLAN])
    executor = _CountingProvider(responses=["closed"])
    second = _graph(planner, executor)
    second._load_checkpoint = _load  # type: ignore[method-assign]
    second._write_checkpoint = _record  # type: ignore[method-assign]
    events: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        events.append(event)

    final = await second.run(
        goal="handle the complaint", tenant_ctx=CTX, goal_id="g1", event_callback=_cb
    )

    assert planner.prompts == [], "resumed goal must reuse the checkpointed plan"
    assert len(executor.prompts) == 1, "only the unfinished step may run again"
    assert "close the loop" in executor.prompts[0]
    assert final.status == GoalStatus.COMPLETE
    resumed = [e["step"] for e in events if e.get("type") == "step_resumed_from_checkpoint"]
    assert resumed == ["file the ticket", "email the customer"]
    outputs = [s.output for s in final.steps if s.status == StepStatus.COMPLETE]
    assert outputs[:2] == ["ticket OPS-1 filed", "email sent"]


def test_restore_ignores_legacy_and_empty_payloads() -> None:
    kw = {"goal": "g", "tenant_ctx": CTX, "goal_id": "g1"}
    assert restore_from_checkpoint(None, None, **kw) is None
    # Pre-fix payload shape (no version, display-only plan): nothing to resume.
    legacy = {"step_index": 1, "plan": ["a", "b"], "iterations": 1}
    assert restore_from_checkpoint(legacy, None, **kw) is None
    state = AgentState(goal="g", tenant_ctx=CTX)
    state.context["_executable_plan"] = ["a"]
    assert restore_from_checkpoint(checkpoint_payload(state, 0), None, **kw) is None


def test_restore_marks_in_flight_steps_failed() -> None:
    state = AgentState(goal="g", tenant_ctx=CTX)
    state.steps = [
        StepResult(description="a", status=StepStatus.COMPLETE, output="ok"),
        StepResult(description="b", status=StepStatus.RUNNING),
    ]
    state.context["_executable_plan"] = ["a", "b"]
    state.context["_ckpt_done"] = {"s0": "ok"}
    restored = restore_from_checkpoint(
        checkpoint_payload(state, 0), None, goal="g", tenant_ctx=CTX, goal_id="g1"
    )
    assert restored is not None
    assert [s.status for s in restored.steps] == [StepStatus.COMPLETE, StepStatus.FAILED]
    assert restored.goal_id == "g1"


async def test_unreadable_checkpoints_fail_closed_instead_of_rerunning() -> None:
    """A checkpoint read error used to be swallowed and the goal started over,
    re-running side-effecting steps. It now fails the goal without executing."""

    async def _broken_load(goal_id: str, tenant_ctx: Any) -> Any:
        raise RuntimeError("checkpoint load failed: db down")

    planner = _CountingProvider(responses=[PLAN])
    executor = _CountingProvider(responses=["sent the email again"])
    graph = _graph(planner, executor)
    graph._load_checkpoint = _broken_load  # type: ignore[method-assign]
    events: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        events.append(event)

    final = await graph.run(
        goal="handle the complaint", tenant_ctx=CTX, goal_id="g-ckpt", event_callback=_cb
    )

    assert final.status is GoalStatus.FAILED
    assert "checkpoint_unavailable" in (final.error_message or "")
    assert planner.prompts == [] and executor.prompts == []
    assert events and events[-1]["type"] == "goal_failed"


async def test_load_checkpoint_db_error_raises_not_none() -> None:
    import pytest

    class _BrokenFactory:
        def __call__(self) -> Any:
            raise ConnectionError("db down")

    graph = _graph(_CountingProvider(responses=[PLAN]), _CountingProvider(responses=["x"]))
    graph._db_session_factory = _BrokenFactory()
    with pytest.raises(RuntimeError, match="checkpoint load failed"):
        await graph._load_checkpoint("g1", CTX)
