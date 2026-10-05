"""P4-1: every workflow create path writes the same ``workflow.created`` audit row.

The live baseline (WF-PUBLISH-APPROVAL) created a workflow through
``POST /v1/workflows/import-yaml`` and found no ``workflow.created`` row: only
``POST /v1/workflows`` called ``record_workflow_action``. Import, clone,
instantiate-from-template, template fork and the NL builder (generate, then
save) must all leave the same row, under the caller's tenant, keyed by the new
workflow id.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.governance.audit import AuditEvent, AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.service import WorkflowService
from app.workflow.template_store import SystemTemplateStore
from tests.workflow.test_service_versions_approval import _RunStore

_T = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_KEY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _ctx(tenant_id: str = _T) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")


def _app(audit: AuditLog) -> TestClient:
    from fastapi import FastAPI, Request

    from app.api.workflows import router as legacy_router
    from app.workflow.router import router
    from app.workflow.router_templates import router as templates_router
    from app.workflow.router_versions import router as versions_router

    store = _WorkflowStore()
    service = WorkflowService(store, run_store=_RunStore())
    app = FastAPI()

    @app.middleware("http")
    async def inject_state(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id=_KEY, roles=("operator",)
        )
        request.app.state.workflow_service = service
        request.app.state.workflow_store = store
        request.app.state.template_store_we = SystemTemplateStore()
        request.app.state.audit_log = audit
        return await call_next(request)

    # Static/versions routes first, as app/bootstrap/routers.py does.
    app.include_router(versions_router, prefix="/api/v1")
    app.include_router(templates_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.include_router(legacy_router)
    return TestClient(app, raise_server_exceptions=False)


def _created(audit: AuditLog, wid: str) -> list[AuditEvent]:
    return [
        e
        for e in audit.query(tenant_ctx=_ctx(), goal_id=wid, limit=100)
        if e.tool_name == "workflow.created"
    ]


def _assert_one_created(audit: AuditLog, wid: str, source: str) -> None:
    rows = _created(audit, wid)
    assert len(rows) == 1, [e.tool_name for e in audit.query(tenant_ctx=_ctx(), limit=100)]
    row = rows[0]
    assert row.outcome == "success"
    assert row.api_key_id == _KEY
    assert f"source={source}" in row.note, row.note
    # Tenant-scoped: nothing lands under another tenant.
    assert audit.query(tenant_ctx=_ctx(_OTHER), limit=100) == []


def _slug() -> str:
    return SystemTemplateStore().all_slugs()[0]


def test_create_via_api_records_source_api() -> None:
    audit = AuditLog()
    client = _app(audit)
    resp = client.post(
        "/api/v1/workflows",
        json={"name": "wf", "definition": {"name": "wf", "steps": []}},
    )
    assert resp.status_code == 201, resp.text
    _assert_one_created(audit, resp.json()["id"], "api")


def test_import_yaml_writes_created_audit_row() -> None:
    audit = AuditLog()
    client = _app(audit)
    body = "name: imported\nsteps:\n  - id: a\n    type: transform\n"
    resp = client.post(
        "/api/v1/workflows/import-yaml",
        content=body.encode(),
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 201, resp.text
    _assert_one_created(audit, str(resp.json()["id"]), "import")


def test_clone_writes_created_audit_row_for_the_copy() -> None:
    audit = AuditLog()
    client = _app(audit)
    orig = client.post(
        "/api/v1/workflows",
        json={"name": "wf", "definition": {"name": "wf", "steps": []}},
    )
    oid = orig.json()["id"]
    resp = client.post(f"/api/v1/workflows/{oid}/clone")
    assert resp.status_code == 201, resp.text
    cid = str(resp.json()["id"])
    assert cid != oid
    _assert_one_created(audit, cid, "clone")
    assert f"cloned_from={oid}" in _created(audit, cid)[0].note


def test_instantiate_template_writes_created_audit_row() -> None:
    audit = AuditLog()
    client = _app(audit)
    slug = _slug()
    resp = client.post(f"/api/v1/workflows/templates/{slug}/instantiate", json={})
    assert resp.status_code == 201, resp.text
    wid = str(resp.json()["id"])
    _assert_one_created(audit, wid, "template")
    assert f"template={slug}" in _created(audit, wid)[0].note


def test_template_fork_writes_created_audit_row() -> None:
    audit = AuditLog()
    client = _app(audit)
    slug = _slug()
    resp = client.post(f"/api/v1/workflow-templates/{slug}/fork", json={"name": "mine"})
    assert resp.status_code == 201, resp.text
    wid = str(resp.json()["id"])
    _assert_one_created(audit, wid, "template")
    assert f"template={slug}" in _created(audit, wid)[0].note


def test_nl_builder_generate_then_save_writes_created_audit_row() -> None:
    """The NL builder generates a canvas (nothing persisted, nothing audited as
    created), then the builder saves through ``POST /workflows``."""
    audit = AuditLog()
    client = _app(audit)
    gen = client.post("/workflows/generate", json={"goal": "summarise the daily sales report"})
    assert gen.status_code == 200, gen.text
    assert [e for e in audit.query(tenant_ctx=_ctx(), limit=100)
            if e.tool_name == "workflow.created"] == []
    resp = client.post(
        "/workflows",
        json={"name": "nl wf", "description": "", "definition": {"name": "nl wf", "steps": []}},
    )
    assert resp.status_code == 201, resp.text
    _assert_one_created(audit, str(resp.json()["id"]), "api")


@pytest.mark.parametrize("bad", ["name: [unterminated", "steps: 3\n"])
def test_rejected_import_writes_no_created_row(bad: str) -> None:
    audit = AuditLog()
    client = _app(audit)
    resp = client.post("/api/v1/workflows/import-yaml", content=bad.encode())
    assert resp.status_code in (400, 422)
    assert [e for e in audit.query(tenant_ctx=_ctx(), limit=100)
            if e.tool_name == "workflow.created"] == []
