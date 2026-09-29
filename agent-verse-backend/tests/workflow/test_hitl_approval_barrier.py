"""An approval (``hitl``) step is a hard barrier.

Regression: when an approval step's actions did not name a ``next`` step, the
compiler wired it with plain edges to its dependents, so every downstream step
ran while the approval was still pending — and a "reject" decision fell through
to the same downstream steps as an approval. Now:

* nothing downstream of a pending approval runs;
* approve lets the downstream steps run;
* reject with no declared reject branch stops the run as FAILED;
* reject with a declared branch routes only to that branch;
* a join step that also depends on a non-approval sibling still waits for the
  approval (the join must not fire from the sibling's edge alone).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import HITLAction, StepDefinition, WorkflowDefinition
from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus

pytestmark = pytest.mark.asyncio


class _RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}
        self.statuses: list[tuple[str, Any, dict[str, Any]]] = []

    def register_definition(self, definition: WorkflowDefinition) -> None:
        self._definitions[definition.id] = definition

    async def create(
        self, *, run_id: str, workflow_id: str, tenant_id: str, **_kwargs: Any
    ) -> None:
        self._runs[run_id] = {"workflow_id": workflow_id, "tenant_id": tenant_id}

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return str(self._runs[run_id]["workflow_id"])

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self._definitions[workflow_id].to_json()

    async def update_status(
        self, run_id: str, status: Any, *, tenant_id: str, **kwargs: Any
    ) -> bool:
        self.statuses.append((run_id, status, kwargs))
        return True

    def last_status(self) -> Any:
        return self.statuses[-1][1] if self.statuses else None


def _build(
    definition: WorkflowDefinition,
) -> tuple[WorkflowRunner, WorkflowCompiler, HITLWorkflowGateway, _RunStore]:
    store = _RunStore()
    store.register_definition(definition)
    gateway = HITLWorkflowGateway()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(), run_store=store, hitl_workflow_gateway=gateway
    )
    runner = WorkflowRunner(compiler=compiler, run_store=store)

    async def _resume(req: WorkflowHITLRequest) -> None:
        await runner.resume_from_hitl(
            run_id=req.run_id,
            step_id=req.step_id,
            action=req.action_taken or "",
            actor_id=req.reviewed_by or "",
            note=req.note,
            form_data=req.form_data,
            tenant_id=req.tenant_id,
        )

    gateway._resume_callback = _resume
    return runner, compiler, gateway, store


def _set(step_id: str, depends_on: list[str], value: str = "x") -> StepDefinition:
    return StepDefinition(
        id=step_id,
        type="set_variable",
        depends_on=depends_on,
        var_name=step_id,
        var_value=value,
    )


def _linear(actions: list[HITLAction]) -> WorkflowDefinition:
    """gate -> after -> final ; gate's actions name no ``next``."""
    return WorkflowDefinition(
        id="wf-barrier",
        name="barrier",
        steps=[
            StepDefinition(id="gate", type="hitl", actions=actions),
            _set("after", ["gate"]),
            _set("final", ["after"]),
        ],
    )


async def _outputs(compiler: WorkflowCompiler, definition: WorkflowDefinition, run_id: str) -> Any:
    compiled = compiler.compile(definition)
    return (await compiled.aget_state({"configurable": {"thread_id": run_id}})).values


@pytest.mark.parametrize(
    "actions",
    [
        [HITLAction(id="approve"), HITLAction(id="reject")],
        [],
    ],
    ids=["declared-actions-without-next", "no-actions"],
)
async def test_downstream_steps_do_not_run_while_approval_is_pending(
    actions: list[HITLAction],
) -> None:
    definition = _linear(actions)
    runner, compiler, _gw, store = _build(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-1", inputs={})

    values = await _outputs(compiler, definition, run_id)
    assert values["status"] == WorkflowRunStatus.WAITING_HITL
    assert "after" not in values["step_outputs"]
    assert "final" not in values["step_outputs"]
    assert store.last_status() == WorkflowRunStatus.WAITING_HITL


async def test_approve_releases_downstream_steps() -> None:
    definition = _linear([HITLAction(id="approve"), HITLAction(id="reject")])
    runner, compiler, gw, store = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-2", inputs={})
    req = (await gw.list_pending(tenant_id="t-2"))[0][0]

    await gw.decide(req.request_id, action="approve", actor_id="r")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert "after" in outs
    assert "final" in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


@pytest.mark.parametrize("action", ["reject", "rejected"])
async def test_reject_without_branch_stops_the_run(action: str) -> None:
    definition = _linear([HITLAction(id="approve"), HITLAction(id="reject")])
    runner, compiler, gw, store = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-3", inputs={})
    req = (await gw.list_pending(tenant_id="t-3"))[0][0]

    await gw.decide(req.request_id, action=action, actor_id="r")

    values = await _outputs(compiler, definition, run_id)
    assert "after" not in values["step_outputs"]
    assert "final" not in values["step_outputs"]
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "reject" in str(store.statuses[-1][2].get("error") or "").lower()


async def test_unrecognised_decision_fails_closed() -> None:
    definition = _linear([HITLAction(id="approve"), HITLAction(id="reject")])
    runner, compiler, gw, store = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-4", inputs={})
    req = (await gw.list_pending(tenant_id="t-4"))[0][0]

    await gw.decide(req.request_id, action="bogus", actor_id="r")

    values = await _outputs(compiler, definition, run_id)
    assert "after" not in values["step_outputs"]
    assert store.last_status() == WorkflowRunStatus.FAILED


async def test_reject_with_declared_branch_routes_only_there() -> None:
    definition = WorkflowDefinition(
        id="wf-reject-branch",
        name="reject-branch",
        steps=[
            StepDefinition(
                id="gate",
                type="hitl",
                actions=[HITLAction(id="approve"), HITLAction(id="reject", next="on_reject")],
            ),
            _set("after", ["gate"]),
            _set("on_reject", ["gate"]),
        ],
    )
    runner, compiler, gw, store = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-5", inputs={})
    assert "after" not in (await _outputs(compiler, definition, run_id))["step_outputs"]
    req = (await gw.list_pending(tenant_id="t-5"))[0][0]

    await gw.decide(req.request_id, action="reject", actor_id="r")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert "on_reject" in outs
    assert "after" not in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


async def test_join_with_sibling_waits_for_the_approval() -> None:
    """``join`` depends on the gate AND a plain sibling: the sibling finishing
    must not trigger the join while the approval is pending."""
    definition = WorkflowDefinition(
        id="wf-join",
        name="join",
        steps=[
            StepDefinition(id="gate", type="hitl", actions=[HITLAction(id="approve")]),
            _set("sibling", []),
            _set("join", ["gate", "sibling"]),
        ],
    )
    runner, compiler, gw, _store = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-6", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert "sibling" in outs
    assert "join" not in outs
