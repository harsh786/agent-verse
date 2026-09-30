"""WF-12: an approval step with no approval gateway fails the run with a clear
error instead of silently entering "test mode" and waiting forever.
"""

from __future__ import annotations

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.runner import WorkflowRunner
from tests.workflow.test_hitl_multi_gate_resume import _RUN, _T, _WF, _HistoryRunStore


def _definition() -> dict[str, object]:
    return WorkflowDefinition(
        name="gate-only",
        id=_WF,
        steps=[
            StepDefinition(id="gate", type="hitl", actions=[{"id": "approve"}]),
            StepDefinition(id="after", type="transform", input={"a": 1}, depends_on=["gate"]),
        ],
    ).to_json()


@pytest.mark.asyncio
async def test_real_run_without_gateway_fails_with_a_descriptive_error() -> None:
    store = _HistoryRunStore(_definition())
    runner = WorkflowRunner(
        compiler=WorkflowCompiler(ContextResolver(), run_store=store), run_store=store
    )
    errors: dict[str, object] = {}
    original = store.update_status

    async def capture(run_id: str, status: object, *, tenant_id: str, **kw: object) -> bool:
        errors.update(kw)
        return await original(run_id, status, tenant_id=tenant_id, **kw)

    store.update_status = capture  # type: ignore[method-assign]

    await runner.execute_fresh(_RUN, _WF, _T)

    assert store.runs[_RUN]["status"] == "failed"
    assert "no approval gateway is configured" in str(errors["error"])
    assert errors["error_step_id"] == "gate"
    assert (await store.get_step_result(_T, _RUN, "gate") or {})["status"] == "failed"
    assert store.executions("after") == 0


@pytest.mark.asyncio
async def test_sandbox_run_without_gateway_still_simulates_the_gate() -> None:
    from app.workflow.steps.hitl_step import HITLStepNode

    step = StepDefinition(id="gate", type="hitl")
    out = await HITLStepNode(step, ContextResolver()).execute(
        {"run_id": "r", "tenant_id": "t", "is_test_run": True}  # type: ignore[typeddict-item]
    )
    assert out["status"] == "waiting_hitl"
