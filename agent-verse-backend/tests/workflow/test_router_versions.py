"""Tests for workflow version history + publishing approval router."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


def make_app(service: MagicMock) -> TestClient:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router_versions import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.workflow_service = service
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def ver_service() -> MagicMock:
    svc = AsyncMock()
    svc.list_versions.return_value = [
        {"version": 1, "created_at": "2026-01-01T00:00:00Z", "status": "published"},
        {"version": 2, "created_at": "2026-01-02T00:00:00Z", "status": "draft"},
    ]
    svc.get_version.return_value = {
        "version": 1, "created_at": "2026-01-01T00:00:00Z",
        "definition": {"name": "Test", "steps": []},
    }
    svc.restore_version.return_value = {
        "id": "wf-1", "name": "Restored WF", "status": "draft",
        "version": "3", "labels": {}, "description": "",
        "created_at": "2026-01-03T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z",
    }
    svc.diff_versions.return_value = {
        "added_steps": ["new_step"],
        "removed_steps": [],
        "modified_steps": ["analyze"],
        "trigger_changed": False,
        "input_changes": [],
    }
    svc.submit_for_approval.return_value = {
        "id": "wf-1", "status": "pending_approval",
        "submitted_at": "2026-01-01T00:00:00Z",
    }
    svc.approve_publish.return_value = {
        "id": "wf-1", "status": "published",
        "publish_approved_by": "admin-1",
        "publish_approved_at": "2026-01-01T00:00:00Z",
    }
    svc.reject_publish.return_value = {
        "id": "wf-1", "status": "draft",
        "rejected_at": "2026-01-01T00:00:00Z",
    }
    svc.get.return_value = {
        "id": "wf-1", "name": "Test WF", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "definition": {"name": "Test WF", "steps": []},
    }
    svc.create.return_value = {
        "id": "wf-imported", "name": "Test WF", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    return svc


@pytest.fixture
def client(ver_service: MagicMock) -> TestClient:
    return make_app(ver_service)


# ── Version list ──────────────────────────────────────────────────────────────


def test_list_versions(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_list_versions_service_unavailable() -> None:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router_versions import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.workflow_service = None
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.get("/api/v1/workflows/wf-1/versions")
    assert resp.status_code == 503


# ── Get specific version ──────────────────────────────────────────────────────


def test_get_version(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/versions/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1


def test_get_version_not_found(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.get_version.return_value = None
    resp = client.get("/api/v1/workflows/wf-1/versions/999")
    assert resp.status_code == 404


# ── Restore version ───────────────────────────────────────────────────────────


def test_restore_version(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/versions/1/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"


def test_restore_version_not_found(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.restore_version.side_effect = ValueError("Version not found")
    resp = client.post("/api/v1/workflows/wf-1/versions/999/restore")
    assert resp.status_code == 404


# ── Diff versions ─────────────────────────────────────────────────────────────


def test_diff_versions(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/versions/1/diff/2")
    assert resp.status_code == 200
    data = resp.json()
    assert "added_steps" in data
    assert "removed_steps" in data
    assert "modified_steps" in data


def test_diff_versions_not_found(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.diff_versions.side_effect = ValueError("Version not found")
    resp = client.get("/api/v1/workflows/wf-1/versions/1/diff/999")
    assert resp.status_code == 404


# ── Publishing approval ────────────────────────────────────────────────────────


def test_submit_for_approval(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/submit-for-approval")
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending_approval"


def test_submit_for_approval_conflict(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.submit_for_approval.side_effect = ValueError("Already submitted")
    resp = client.post("/api/v1/workflows/wf-1/submit-for-approval")
    assert resp.status_code == 409


def test_approve_publish(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/approve-publish", json={
        "note": "LGTM", "approver_id": "admin-1"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "published"
    assert data["publish_approved_by"] == "admin-1"


def test_reject_publish(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/reject-publish", json={
        "note": "Needs more review"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"


# ── YAML export ───────────────────────────────────────────────────────────────


def test_export_yaml(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/yaml")
    assert resp.status_code == 200
    # Should return YAML string
    body = resp.text
    assert len(body) > 0


def test_export_yaml_not_found(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.get.return_value = None
    resp = client.get("/api/v1/workflows/bad-id/yaml")
    assert resp.status_code == 404


# ── Import YAML ───────────────────────────────────────────────────────────────


def test_import_yaml(client: TestClient) -> None:
    yaml_body = b"name: Test WF\nsteps: []\n"
    resp = client.post("/api/v1/workflows/import-yaml",
                       content=yaml_body,
                       headers={"Content-Type": "text/plain"})
    assert resp.status_code in (201, 422)  # 422 if DSL validation fails on minimal yaml


def test_import_invalid_yaml(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/import-yaml",
                       content=b"{ invalid yaml {{{{",
                       headers={"Content-Type": "text/plain"})
    assert resp.status_code == 400


# ── Clone ─────────────────────────────────────────────────────────────────────


def test_clone_workflow(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/clone")
    assert resp.status_code == 201
    data = resp.json()
    assert "copy" in data["name"].lower() or data["id"] != "wf-1"


def test_clone_not_found(client: TestClient, ver_service: MagicMock) -> None:
    ver_service.get.return_value = None
    resp = client.post("/api/v1/workflows/bad-id/clone")
    assert resp.status_code == 404


# ── WF-ROUTES-500: the routes against the REAL WorkflowService ────────────────
# The fixtures above use an AsyncMock service, which answers any method name —
# that is how routes calling methods that did not exist (500 in production)
# passed. These drive the real service.


def _real_client() -> tuple[TestClient, str]:
    import asyncio

    from app.api.workflows import _WorkflowStore
    from app.workflow.service import WorkflowService

    svc = WorkflowService(_WorkflowStore(), run_store=None)
    wf = asyncio.run(
        svc.create(tenant_id="test-tenant", name="wf", definition={"name": "wf", "steps": []})
    )
    return make_app(svc), str(wf["id"])


def test_real_service_version_detail_is_404_not_500() -> None:
    client, wid = _real_client()
    assert client.get(f"/api/v1/workflows/{wid}/versions/1").status_code == 404


def test_real_service_diff_unknown_versions_is_404_not_500() -> None:
    client, wid = _real_client()
    assert client.get(f"/api/v1/workflows/{wid}/versions/1/diff/2").status_code == 404


def test_real_service_approval_without_persistence_is_503_not_500() -> None:
    client, wid = _real_client()
    for path in ("submit-for-approval", "approve-publish", "reject-publish"):
        resp = client.post(f"/api/v1/workflows/{wid}/{path}", json={})
        assert resp.status_code == 503, (path, resp.status_code, resp.text)


def test_approver_is_the_caller_not_the_request_body(
    client: TestClient, ver_service: MagicMock
) -> None:
    resp = client.post(
        "/api/v1/workflows/wf-1/approve-publish",
        json={"note": "LGTM", "approver_id": "someone-else"},
    )
    assert resp.status_code == 200
    assert ver_service.approve_publish.await_args.kwargs["approver_id"] == "key-1"


def test_submitter_is_the_caller(client: TestClient, ver_service: MagicMock) -> None:
    resp = client.post("/api/v1/workflows/wf-1/submit-for-approval")
    assert resp.status_code == 202
    assert ver_service.submit_for_approval.await_args.kwargs["submitted_by"] == "key-1"
