"""WF-40: a failed durable approval write is never swallowed.

``_save`` logged and ignored a failed ``approval_store.save``: a worker-created
approval existed only in that task's memory while the run sat in waiting_hitl
forever, and delegate / escalate reported success for unsaved changes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.workflow.hitl_extension import (
    ApprovalPersistenceError,
    HITLWorkflowGateway,
    WorkflowHITLRequest,
)

_T = "11111111-1111-1111-1111-111111111111"


class _BrokenStore:
    def __init__(self, existing: WorkflowHITLRequest | None = None) -> None:
        self.existing = existing

    async def save(self, req: WorkflowHITLRequest) -> None:
        raise ConnectionError("workflow_approvals is locked")

    async def get(self, request_id: str, tenant_id: str) -> WorkflowHITLRequest | None:
        return self.existing


@pytest.mark.asyncio
async def test_create_raises_and_leaves_no_phantom_approval() -> None:
    gw = HITLWorkflowGateway(approval_store=_BrokenStore())
    with pytest.raises(ApprovalPersistenceError):
        await gw.create_workflow_approval(
            run_id="r1", step_id="gate", tenant_id=_T, strategy="specific", specific_user="u"
        )
    assert gw._store == {}


@pytest.mark.asyncio
async def test_hitl_step_failure_does_not_park_the_run_waiting() -> None:
    from app.workflow.compiler import WorkflowCompiler
    from app.workflow.context import ContextResolver
    from app.workflow.dsl import StepDefinition, WorkflowDefinition
    from app.workflow.state import WorkflowRunStatus

    gw = HITLWorkflowGateway(approval_store=_BrokenStore())
    definition = WorkflowDefinition(
        name="g",
        id="wf",
        steps=[StepDefinition(id="gate", type="hitl", actions=[{"id": "approve"}])],
    )
    compiled = WorkflowCompiler(ContextResolver(), hitl_workflow_gateway=gw).compile(definition)
    state: dict[str, Any] = {
        "run_id": "r1", "tenant_id": _T, "workflow_id": "wf", "step_outputs": {}, "vars": {},
        "inputs": {}, "status": WorkflowRunStatus.PENDING,
    }
    final = await compiled.ainvoke(state, {"configurable": {"thread_id": "r1"}})  # type: ignore[arg-type]
    assert final.get("status") != WorkflowRunStatus.WAITING_HITL
    assert "approval" in str(final.get("error") or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["delegate", "escalate", "add_comment"])
async def test_mutations_raise_when_the_write_fails(op: str) -> None:
    existing = WorkflowHITLRequest(run_id="r1", step_id="gate", tenant_id=_T)
    gw = HITLWorkflowGateway(approval_store=_BrokenStore(existing))
    with pytest.raises(ApprovalPersistenceError):
        if op == "delegate":
            await gw.delegate(existing.request_id, "a", "b", tenant_id=_T)
        elif op == "escalate":
            await gw.escalate(existing.request_id, "a", tenant_id=_T)
        else:
            await gw.add_comment(existing.request_id, "a", "hi", tenant_id=_T)


def test_delegate_and_escalate_routes_return_503() -> None:
    from app.tenancy.context import PlanTier, TenantContext
    from app.workflow.router_hitl import router

    existing = WorkflowHITLRequest(
        run_id="r1", step_id="gate", tenant_id=_T, assigned_to="owner"
    )
    gw = HITLWorkflowGateway(approval_store=_BrokenStore(existing))
    app = FastAPI()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.PROFESSIONAL, api_key_id="owner", roles=("admin",)
        )
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    app.state.hitl_workflow_gateway = gw
    client = TestClient(app)
    rid = existing.request_id
    resp = client.post(f"/api/v1/approvals/{rid}/delegate", json={"to_user_id": "b"})
    assert resp.status_code == 503, resp.text
    assert "try again" in resp.json()["detail"]
    resp = client.post(f"/api/v1/approvals/{rid}/escalate")
    assert resp.status_code == 503, resp.text
