"""Tests for the main workflow engine router (CRUD + publish + trigger)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# ── Minimal FastAPI test app ───────────────────────────────────────────────────


def make_app(service: MagicMock) -> TestClient:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass  # skip validation
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.workflow_service = service
        request.app.state.workflow_runner = None
        request.app.state.nl_trigger_resolver = None
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def wf_service() -> MagicMock:
    svc = AsyncMock()
    svc.create.return_value = {
        "id": "wf-1", "name": "Test WF", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    svc.get.return_value = {
        "id": "wf-1", "name": "Test WF", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "definition": {"name": "Test WF", "steps": []},
    }
    svc.list.return_value = (
        [{"id": "wf-1", "name": "Test WF", "status": "draft",
          "version": "1", "labels": {}, "description": "",
          "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}],
        1
    )
    svc.update.return_value = {
        "id": "wf-1", "name": "Updated", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    svc.archive.return_value = True
    svc.publish.return_value = {
        "id": "wf-1", "name": "Test WF", "status": "published",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    svc.unpublish.return_value = {
        "id": "wf-1", "name": "Test WF", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    svc.list_versions.return_value = [{"version": "1", "created_at": "2026-01-01T00:00:00Z"}]
    svc.restore_version.return_value = {"id": "wf-1", "version": "2"}
    svc.get_permissions.return_value = []
    svc.add_permission.return_value = {"id": "perm-1", "subject": "user-1", "role": "viewer"}
    svc.remove_permission.return_value = True
    svc.list_templates.return_value = ([], 0)
    svc.get_template.return_value = {"slug": "kyc-automation", "name": "KYC", "category": "Financial"}
    svc.instantiate_template.return_value = {
        "id": "wf-new", "name": "KYC", "status": "draft",
        "version": "1", "labels": {}, "description": "",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    svc.analytics_summary.return_value = {"total_runs": 0}
    svc.workflow_analytics.return_value = {"total_runs": 0}
    svc.list_webhook_events.return_value = ([], 0)
    svc.marketplace_list.return_value = ([], 0)
    return svc


@pytest.fixture
def client(wf_service: MagicMock) -> TestClient:
    return make_app(wf_service)


# ── CRUD endpoints ────────────────────────────────────────────────────────────


def test_create_workflow(client: TestClient, wf_service: MagicMock) -> None:
    resp = client.post("/api/v1/workflows", json={
        "name": "My Workflow",
        "definition": {"name": "My Workflow", "steps": []},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "wf-1"
    assert data["name"] == "Test WF"


def test_create_workflow_invalid_dsl(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows", json={
        "name": "Bad WF",
        "definition": {"steps": [{"id": "a", "depends_on": ["nonexistent"]}]},
    })
    assert resp.status_code in (400, 422)


def test_list_workflows(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 0


def test_get_workflow(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "wf-1"


def test_get_workflow_not_found(client: TestClient, wf_service: MagicMock) -> None:
    wf_service.get.return_value = None
    resp = client.get("/api/v1/workflows/nonexistent")
    assert resp.status_code == 404


def test_update_workflow(client: TestClient) -> None:
    resp = client.patch("/api/v1/workflows/wf-1", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


def test_delete_workflow(client: TestClient) -> None:
    resp = client.delete("/api/v1/workflows/wf-1")
    assert resp.status_code == 204


def test_delete_workflow_not_found(client: TestClient, wf_service: MagicMock) -> None:
    wf_service.archive.return_value = False
    resp = client.delete("/api/v1/workflows/nonexistent")
    assert resp.status_code == 404


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_publish_workflow(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


def test_publish_not_found(client: TestClient, wf_service: MagicMock) -> None:
    wf_service.publish.return_value = None
    resp = client.post("/api/v1/workflows/bad/publish")
    assert resp.status_code == 404


def test_unpublish_workflow(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/unpublish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"


# ── Validation ────────────────────────────────────────────────────────────────


def test_validate_workflow_valid(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/validate")
    assert resp.status_code == 200
    data = resp.json()
    assert "valid" in data
    assert "errors" in data


def test_validate_workflow_not_found(client: TestClient, wf_service: MagicMock) -> None:
    wf_service.get.return_value = None
    resp = client.post("/api/v1/workflows/bad/validate")
    assert resp.status_code == 404


# ── NL trigger preview ────────────────────────────────────────────────────────


def test_nl_trigger_preview_no_resolver(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/nl-trigger-preview", json={"description": "every minute"})
    assert resp.status_code == 503  # resolver not configured


# ── Templates ────────────────────────────────────────────────────────────────


def test_list_templates(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/templates")
    assert resp.status_code == 200


def test_get_template_found(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/templates/kyc-automation")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "kyc-automation"


def test_get_template_not_found(client: TestClient, wf_service: MagicMock) -> None:
    wf_service.get_template.return_value = None
    resp = client.get("/api/v1/workflows/templates/bad-slug")
    assert resp.status_code == 404


# ── Analytics ─────────────────────────────────────────────────────────────────


def test_analytics_summary(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/analytics/summary")
    assert resp.status_code == 200


def test_workflow_analytics(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/analytics")
    assert resp.status_code == 200


# ── Permissions ───────────────────────────────────────────────────────────────


def test_list_permissions(client: TestClient) -> None:
    resp = client.get("/api/v1/workflows/wf-1/permissions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_add_permission(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/wf-1/permissions", json={
        "subject": "user-1", "role": "viewer"
    })
    assert resp.status_code == 201


def test_remove_permission(client: TestClient) -> None:
    resp = client.delete("/api/v1/workflows/wf-1/permissions/perm-1")
    assert resp.status_code == 204
