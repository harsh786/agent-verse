"""WS-3: full HITL pause -> approve -> resume for a real workflow run.

Exercises the actual (non-mocked) chain end to end, in-process:

  WorkflowRunner.run() suspends a ``hitl`` step
    -> HITLStepNode.execute() calls HITLWorkflowGateway.create_workflow_approval()
    -> a real pending request is stored, reachable via list_pending (the
       ``/approvals`` inbox)
    -> deciding it (HITLWorkflowGateway.decide) fires the wired resume_callback
    -> WorkflowRunner.resume_from_hitl() re-invokes the paused LangGraph
       checkpoint, which now processes the reviewer's decision.

This is the exact wiring that was broken before WS-3:
  * ``create_workflow_approval`` did not exist on HITLWorkflowGateway at all
    (AttributeError the first time any real HITL step tried to suspend).
  * WorkflowCompiler was never constructed with a ``hitl_workflow_gateway``
    service, so HITLStepNode always fell back to its no-gateway "test mode"
    branch and never created a real approval request.
  * HITLWorkflowGateway was constructed with no ``resume_callback``, so a
    decided approval never actually resumed the paused run.
  * ``WorkflowRunner.resume_from_hitl`` cleared ``hitl_request_id`` to
    ``None`` instead of the step id, so even a correctly wired resume would
    re-suspend the step instead of processing the decision.

Runs fully in-process with a MemorySaver checkpointer, no Celery, and a
minimal in-memory run-store double — no Postgres/Redis needed.
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


class _FakeRunStore:
    """Minimal in-memory WorkflowRunStore double.

    Implements only the methods the trigger/resume paths under test actually
    call — this is not a full ``WorkflowRunStore`` Protocol implementation.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}

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
        self, run_id: str, status: Any, *, tenant_id: str, **_kwargs: Any
    ) -> bool:
        return True


def _make_resume_callback(runner: WorkflowRunner) -> Any:
    """Mirror app.main._make_workflow_hitl_resume_callback for test isolation."""

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

    return _resume


def _hitl_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf-hitl-1",
        name="hitl-gate",
        steps=[
            StepDefinition(
                id="gate",
                type="hitl",
                actions=[HITLAction(id="approve"), HITLAction(id="reject")],
            ),
        ],
    )


def _build_runner(
    definition: WorkflowDefinition,
) -> tuple[WorkflowRunner, WorkflowCompiler, HITLWorkflowGateway, _FakeRunStore]:
    run_store = _FakeRunStore()
    run_store.register_definition(definition)
    hitl_gateway = HITLWorkflowGateway()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        run_store=run_store,
        hitl_workflow_gateway=hitl_gateway,
    )
    runner = WorkflowRunner(compiler=compiler, run_store=run_store)
    hitl_gateway._resume_callback = _make_resume_callback(runner)
    return runner, compiler, hitl_gateway, run_store


async def test_workflow_hitl_pause_approve_resume() -> None:
    definition = _hitl_workflow_definition()
    runner, compiler, hitl_gateway, _store = _build_runner(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-1", inputs={})

    # 1. The run suspended at the hitl step; a real approval request exists
    #    and is reachable via the approvals-inbox listing.
    pending, total = await hitl_gateway.list_pending(tenant_id="t-1")
    assert total == 1
    req = pending[0]
    assert req.run_id == run_id
    assert req.step_id == "gate"
    assert req.status == "pending"

    config = {"configurable": {"thread_id": run_id}}
    compiled = compiler.compile(definition)
    state = await compiled.aget_state(config)
    assert state.values["status"] == WorkflowRunStatus.WAITING_HITL

    # 2. Approve via the gateway — must resume the paused run for real.
    decided = await hitl_gateway.decide(req.request_id, action="approve", actor_id="reviewer-1")
    # WorkflowHITLRequest.decide() only special-cases the literal strings
    # "approved"/"rejected"; a custom action id like this step's "approve"
    # resolves to the generic "decided" status.
    assert decided.status == "decided"
    assert decided.action_taken == "approve"

    state = await compiled.aget_state(config)
    assert state.values["status"] != WorkflowRunStatus.WAITING_HITL
    assert state.values["step_outputs"]["gate"]["action"] == "approve"
    assert state.values["step_outputs"]["gate"]["reviewer"] == "reviewer-1"

    # 3. The approval is resolved — no longer in the pending inbox.
    _pending_after, total_after = await hitl_gateway.list_pending(tenant_id="t-1")
    assert total_after == 0


async def test_workflow_hitl_reject_does_not_advance_as_approved() -> None:
    definition = _hitl_workflow_definition()
    runner, compiler, hitl_gateway, _store = _build_runner(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-2", inputs={})
    pending, _ = await hitl_gateway.list_pending(tenant_id="t-2")
    req = pending[0]

    await hitl_gateway.decide(req.request_id, action="reject", actor_id="reviewer-2")

    config = {"configurable": {"thread_id": run_id}}
    compiled = compiler.compile(definition)
    state = await compiled.aget_state(config)
    assert state.values["step_outputs"]["gate"]["action"] == "reject"
    assert state.values["status"] != WorkflowRunStatus.WAITING_HITL
