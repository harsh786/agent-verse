"""Engine/orchestration-layer coverage for the workflow compiler+graph:

  - step-to-step data passing through the REAL compiled LangGraph (not just
    ContextResolver in isolation with a hand-built state dict)
  - workflow-level error propagation through the full graph (on_failure="abort")
  - cancellation partway through a run (after a step has already completed),
    not just before the first step

Individual step-type behaviors are covered elsewhere (test_steps/); this file
targets the compiler's node wrapper, edge wiring, and run-control integration
acting together as the orchestration layer.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.state import WorkflowCancelled

pytestmark = pytest.mark.asyncio


def _state(**overrides: Any) -> dict[str, Any]:
    base = {
        "run_id": "run-1",
        "tenant_id": "t-1",
        "is_test_run": False,
        "inputs": {},
        "step_outputs": {},
        "step_timings": {},
        "vars": {},
    }
    base.update(overrides)
    return base


def _cfg(thread_id: str = "run-1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ── Step-to-step data passing through the real graph ───────────────────────────


async def test_downstream_step_consumes_upstream_step_output() -> None:
    """A two-step 'transform' graph: step 'b' templates {{steps.a.output.x}} and
    must see the value step 'a' actually produced, via the real StateGraph
    step_outputs reducer — not a hand-assembled state dict."""
    wf = WorkflowDefinition(
        name="pipe",
        steps=[
            StepDefinition(id="a", type="transform", input={"x": 21}),
            StepDefinition(
                id="b",
                type="transform",
                input={"doubled": "{{steps.a.output.x}}", "note": "from-a"},
                depends_on=["a"],
            ),
        ],
    )
    compiler = WorkflowCompiler(ContextResolver())
    compiled = compiler.compile(wf)

    final = await compiled.ainvoke(_state(), _cfg())

    assert final["step_outputs"]["a"] == {"x": 21}
    assert final["step_outputs"]["b"] == {"doubled": 21, "note": "from-a"}


async def test_three_step_chain_propagates_across_two_hops() -> None:
    """Verifies data survives more than one hop (a → b → c), i.e. the reducer
    keeps accumulating rather than only exposing the immediately-prior step."""
    wf = WorkflowDefinition(
        name="chain",
        steps=[
            StepDefinition(id="a", type="transform", input={"v": 1}),
            StepDefinition(
                id="b", type="transform", input={"v": "{{steps.a.output.v}}"}, depends_on=["a"]
            ),
            StepDefinition(
                id="c",
                type="transform",
                input={"from_a": "{{steps.a.output.v}}", "from_b": "{{steps.b.output.v}}"},
                depends_on=["b"],
            ),
        ],
    )
    compiler = WorkflowCompiler(ContextResolver())
    compiled = compiler.compile(wf)

    final = await compiled.ainvoke(_state(), _cfg())

    assert final["step_outputs"]["c"] == {"from_a": 1, "from_b": 1}


# ── Workflow-level error propagation through the full graph ────────────────────


async def test_abort_policy_propagates_through_ainvoke_and_skips_downstream() -> None:
    """on_failure="abort" must raise out of the compiled graph's ainvoke (not
    just the node function in isolation), and the downstream step must never
    run."""
    from app.workflow.registry import StepTypeRegistry

    calls = {"b_ran": False}

    class _BoomNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("upstream exploded")

    class _RecordingNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            calls["b_ran"] = True
            return {"step_outputs": {"b": {"ran": True}}}

    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    try:
        StepTypeRegistry._registry["boom_abort_e2e"] = _BoomNode  # type: ignore[attr-defined]
        StepTypeRegistry._registry["recording_e2e"] = _RecordingNode  # type: ignore[attr-defined]

        wf = WorkflowDefinition(
            name="abort-e2e",
            steps=[
                StepDefinition(id="a", type="boom_abort_e2e", on_failure="abort"),
                StepDefinition(id="b", type="recording_e2e", depends_on=["a"]),
            ],
        )
        compiler = WorkflowCompiler(ContextResolver())
        compiled = compiler.compile(wf)

        with pytest.raises(RuntimeError, match="upstream exploded"):
            await compiled.ainvoke(_state(), _cfg())

        assert calls["b_ran"] is False
    finally:
        StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


async def test_pause_policy_halts_before_downstream_step_runs() -> None:
    """The default on_failure="pause" must stop the run — downstream steps must
    not execute after the failing step pauses (distinct from "abort": no
    exception, but forward progress still must not happen)."""
    from app.workflow.registry import StepTypeRegistry

    calls = {"b_ran": False}

    class _BoomNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("degraded upstream")

    class _RecordingNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            calls["b_ran"] = True
            return {"step_outputs": {"b": {"ran": True}}}

    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    try:
        StepTypeRegistry._registry["boom_pause_e2e"] = _BoomNode  # type: ignore[attr-defined]
        StepTypeRegistry._registry["recording_e2e2"] = _RecordingNode  # type: ignore[attr-defined]

        wf = WorkflowDefinition(
            name="pause-e2e",
            steps=[
                StepDefinition(id="a", type="boom_pause_e2e"),  # default on_failure="pause"
                StepDefinition(id="b", type="recording_e2e2", depends_on=["a"]),
            ],
        )
        compiler = WorkflowCompiler(ContextResolver())
        compiled = compiler.compile(wf)

        final = await compiled.ainvoke(_state(), _cfg())

        assert final["status"] == "paused"
        assert final["error_step_id"] == "a"
        assert calls["b_ran"] is False
    finally:
        StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


# ── Cancellation partway through a run (not just before the first step) ────────


class _SequencedRunStore:
    """Returns a different status per call — lets a test cancel the run only
    AFTER an earlier step has already been checked/executed, modelling an
    operator hitting "cancel" while a multi-step run is mid-flight."""

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        idx = min(self.calls, len(self._statuses) - 1)
        self.calls += 1
        return self._statuses[idx]


async def test_cancellation_after_first_step_completes_stops_second_step() -> None:
    """Step 'a' must run to completion (status='running' on its check), and
    only THEN does the operator's cancel take effect before step 'b' starts —
    a genuinely mid-run cancellation, distinct from cancelling before step one
    ever executes."""
    run_store = _SequencedRunStore(statuses=["running", "cancelled"])
    wf = WorkflowDefinition(
        name="mid-cancel",
        steps=[
            StepDefinition(id="a", type="transform", input={"v": 1}),
            StepDefinition(id="b", type="transform", input={"v": 2}, depends_on=["a"]),
        ],
    )
    compiler = WorkflowCompiler(ContextResolver(), run_store=run_store)
    compiled = compiler.compile(wf)

    with pytest.raises(WorkflowCancelled):
        await compiled.ainvoke(_state(), _cfg())

    # The cancellation check ran at least twice: once before 'a' (saw
    # "running", proceeded) and once before 'b' (saw "cancelled", raised).
    assert run_store.calls >= 2
