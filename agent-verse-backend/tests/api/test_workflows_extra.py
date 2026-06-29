"""Extra coverage for app/api/workflows.py — workflow CRUD and run endpoints."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.api.workflows import router as workflows_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-wf-extra", plan=PlanTier.ENTERPRISE, api_key_id="kid-wf")
_VALID_KEY = "av_test_wf_extra"


def _make_app(with_store: bool = True, goal_service=None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)

    if with_store:
        from app.api.workflows import _WorkflowStore
        app.state.workflow_store = _WorkflowStore()
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


_H = {"X-API-Key": _VALID_KEY}


class TestWorkflowCrud:
    def test_list_workflows_empty(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/workflows", headers=_H)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/workflows",
            json={
                "name": "My Workflow",
                "description": "Test workflow",
                "definition": {
                    "nodes": [{"id": "n1", "type": "start"}],
                    "edges": [],
                },
            },
            headers=_H,
        )
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert "id" in body
        assert body["name"] == "My Workflow"

    def test_get_workflow_by_id(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/workflows",
            json={"name": "Retrievable Workflow"},
            headers=_H,
        )
        wf_id = create_resp.json()["id"]
        resp = client.get(f"/workflows/{wf_id}", headers=_H)
        assert resp.status_code == 200
        assert resp.json()["id"] == wf_id

    def test_get_nonexistent_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/workflows/nonexistent-id", headers=_H)
        assert resp.status_code == 404

    def test_update_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/workflows",
            json={"name": "Original Name", "definition": {}},
            headers=_H,
        )
        wf_id = create_resp.json()["id"]
        resp = client.put(
            f"/workflows/{wf_id}",
            json={"name": "Updated Name", "definition": {"nodes": []}},
            headers=_H,
        )
        assert resp.status_code in (200, 204)
        if resp.status_code == 200:
            assert resp.json()["name"] == "Updated Name"

    def test_update_nonexistent_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.put(
            "/workflows/nonexistent",
            json={"name": "X", "definition": {}},
            headers=_H,
        )
        assert resp.status_code == 404

    def test_delete_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/workflows",
            json={"name": "To Delete"},
            headers=_H,
        )
        wf_id = create_resp.json()["id"]
        resp = client.delete(f"/workflows/{wf_id}", headers=_H)
        assert resp.status_code in (200, 204)

    def test_delete_nonexistent_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.delete("/workflows/ghost", headers=_H)
        assert resp.status_code == 404

    def test_list_after_create(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        client.post("/workflows", json={"name": "WF1"}, headers=_H)
        client.post("/workflows", json={"name": "WF2"}, headers=_H)
        resp = client.get("/workflows", headers=_H)
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_workflows_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/workflows")
        assert resp.status_code == 401

    def test_no_store_returns_503(self):
        client = TestClient(_make_app(with_store=False), raise_server_exceptions=False)
        resp = client.get("/workflows", headers=_H)
        assert resp.status_code == 503


class TestWorkflowRun:
    def test_run_workflow_dry_run(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/workflows",
            json={
                "name": "Runnable Workflow",
                "definition": {
                    "nodes": [
                        {"id": "n1", "type": "tool", "data": {"tool": "github:list_issues"}},
                    ],
                    "edges": [],
                },
            },
            headers=_H,
        )
        wf_id = create_resp.json()["id"]
        resp = client.post(f"/workflows/{wf_id}/run", json={"dry_run": True}, headers=_H)
        assert resp.status_code in (200, 201, 202)
        body = resp.json()
        assert "status" in body or "goal_id" in body

    def test_run_nonexistent_workflow(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/workflows/ghost/run", json={}, headers=_H)
        assert resp.status_code == 404
