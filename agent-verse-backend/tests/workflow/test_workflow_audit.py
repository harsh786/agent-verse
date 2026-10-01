"""WF-AUDIT: workflow lifecycle actions write tenant-scoped audit events.

``AutoAuditMiddleware._emit`` called ``audit_log.record(tenant_id, event)`` —
``AuditLog.record`` takes ``(event, *, tenant_ctx=...)`` — so every emit raised
``TypeError``, was swallowed, and no workflow audit event was ever written.
Nothing called the middleware from the API either.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.audit_middleware import AutoAuditMiddleware
from app.workflow.service import WorkflowService
from tests.workflow.test_service_versions_approval import _RunStore

_T = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"


def _ctx(tenant_id: str = _T) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")


def _tools(audit: AuditLog, tenant_id: str = _T) -> list[str]:
    return [e.tool_name for e in audit.query(tenant_ctx=_ctx(tenant_id), limit=1000)]


def test_middleware_emit_reaches_the_audit_log() -> None:
    audit = AuditLog()
    mw = AutoAuditMiddleware(audit)
    mw.run_started({"run_id": "run-1", "workflow_id": "wf-1", "tenant_id": _T, "inputs": {}})  # type: ignore[arg-type]
    mw.step_failed({"run_id": "run-1", "tenant_id": _T}, step_id="s1", error="boom")  # type: ignore[arg-type]
    events = audit.query(tenant_ctx=_ctx(), limit=10)
    assert [e.tool_name for e in events] == [
        "workflow.workflow.run_started",
        "workflow.step.failed",
    ]
    assert events[1].step_id == "s1"
    assert "boom" in events[1].note
    assert audit.query(tenant_ctx=_ctx(_OTHER), limit=10) == []


class _Runner:
    async def trigger(self, **kwargs: Any) -> dict[str, Any]:
        return {"run_id": "run-42", "workflow_id": kwargs["workflow_id"], "status": "pending"}


def _app(audit: AuditLog) -> TestClient:
    from fastapi import FastAPI, Request

    from app.workflow.router import router
    from app.workflow.router_versions import router as versions_router

    service = WorkflowService(_WorkflowStore(), run_store=_RunStore())
    app = FastAPI()

    @app.middleware("http")
    async def inject_state(request: Request, call_next: Any) -> Any:
        key = request.headers.get("x-test-key", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id=key, roles=("operator",)
        )
        request.app.state.workflow_service = service
        request.app.state.workflow_runner = _Runner()
        request.app.state.audit_log = audit
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    app.include_router(versions_router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_create_update_publish_run_and_approve_are_audited() -> None:
    audit = AuditLog()
    client = _app(audit)
    definition = {"name": "wf", "steps": [{"id": "a", "type": "transform"}]}

    created = client.post("/api/v1/workflows", json={"name": "wf", "definition": definition})
    assert created.status_code == 201, created.text
    wid = created.json()["id"]
    assert client.patch(f"/api/v1/workflows/{wid}", json={"description": "d"}).status_code == 200
    assert client.post(f"/api/v1/workflows/{wid}/publish").status_code == 200
    trig = client.post(f"/api/v1/workflows/{wid}/trigger", json={"inputs": {}})
    assert trig.status_code == 202, trig.text
    assert client.post(f"/api/v1/workflows/{wid}/unpublish").status_code == 200

    on = client.patch(f"/api/v1/workflows/{wid}", json={"requires_publish_approval": True})
    assert on.status_code == 200, on.text
    assert client.post(f"/api/v1/workflows/{wid}/submit-for-approval").status_code == 202
    approved = client.post(
        f"/api/v1/workflows/{wid}/approve-publish",
        json={"note": "ok"},
        headers={"x-test-key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
    )
    assert approved.status_code == 200, approved.text

    events = audit.query(tenant_ctx=_ctx(), goal_id=wid, limit=100)
    tools = [e.tool_name for e in events]
    for expected in (
        "workflow.created",
        "workflow.updated",
        "workflow.published",
        "workflow.run_triggered",
        "workflow.unpublished",
        "workflow.publish_submitted",
        "workflow.publish_approved",
    ):
        assert expected in tools, (expected, tools)
    run = next(e for e in events if e.tool_name == "workflow.run_triggered")
    assert "run-42" in run.note
    approval = next(e for e in events if e.tool_name == "workflow.publish_approved")
    assert approval.approver == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert approval.api_key_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    # Tenant-scoped: nothing lands under another tenant.
    assert _tools(audit, _OTHER) == []


def test_refused_publish_is_audited_as_denied() -> None:
    audit = AuditLog()
    client = _app(audit)
    created = client.post(
        "/api/v1/workflows", json={"name": "wf", "definition": {"name": "wf", "steps": []}}
    )
    wid = created.json()["id"]
    client.patch(f"/api/v1/workflows/{wid}", json={"requires_publish_approval": True})
    assert client.post(f"/api/v1/workflows/{wid}/publish").status_code == 409
    events = [
        e
        for e in audit.query(tenant_ctx=_ctx(), goal_id=wid, limit=100)
        if e.tool_name == "workflow.published"
    ]
    assert [e.outcome for e in events] == ["denied"]


@pytest.mark.asyncio
async def test_hitl_decision_is_audited() -> None:
    from fastapi import FastAPI, Request
    from httpx import ASGITransport, AsyncClient

    from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest
    from app.workflow.router_hitl import router

    audit = AuditLog()
    gateway = HITLWorkflowGateway()
    req = await gateway.create_request(
        WorkflowHITLRequest(
            run_id="run-7", workflow_id="wf-7", tenant_id=_T, step_id="gate",
            assignment_strategy="specific_user", assigned_to="reviewer-1",
        )
    )
    app = FastAPI()

    @app.middleware("http")
    async def inject_state(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="reviewer-1", roles=("approver",)
        )
        request.app.state.hitl_workflow_gateway = gateway
        request.app.state.audit_log = audit
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/decide", json={"action": "approve"}
        )
    assert resp.status_code == 200, resp.text
    events = audit.query(tenant_ctx=_ctx(), goal_id="wf-7", limit=10)
    assert [(e.tool_name, e.outcome, e.approver) for e in events] == [
        ("workflow.approval_decided", "approve", "reviewer-1")
    ]
    assert "run-7" in events[0].note and req.request_id in events[0].note
