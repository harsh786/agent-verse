"""Comprehensive tests for /workflows API endpoints — targets 29% → 65%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import router as workflows_router, _WorkflowStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-workflows", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_workflows_comp"


def _make_app(workflow_store: _WorkflowStore | None = None, goal_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = workflow_store or _WorkflowStore()
    if goal_service:
        app.state.goal_service = goal_service
    return app


def _sample_definition() -> dict:
    return {
        "nodes": [
            {"id": "n1", "type": "trigger", "label": "Start"},
            {"id": "n2", "type": "action", "label": "Deploy"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_workflows_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/workflows")
    assert resp.status_code == 401


def test_create_workflow_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/workflows", json={"name": "test"})
    assert resp.status_code == 401


def test_get_workflow_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/workflows/wf-1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# No store configured
# ---------------------------------------------------------------------------

def test_list_workflows_no_store() -> None:
    app = FastAPI()
    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    # Do NOT set workflow_store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/workflows", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# list_workflows
# ---------------------------------------------------------------------------

def test_list_workflows_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/workflows", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_workflows_after_create() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    client.post(
        "/workflows",
        json={"name": "Deploy Pipeline", "description": "CD workflow"},
        headers={"X-API-Key": _VALID_KEY},
    )
    resp = client.get("/workflows", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    wfs = resp.json()
    assert len(wfs) == 1
    assert wfs[0]["name"] == "Deploy Pipeline"


# ---------------------------------------------------------------------------
# create_workflow
# ---------------------------------------------------------------------------

def test_create_workflow_minimal() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={"name": "My Workflow"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Workflow"
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert "id" in body


def test_create_workflow_with_definition() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={
            "name": "CI/CD Flow",
            "description": "Continuous delivery",
            "definition": _sample_definition(),
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["definition"]["nodes"][0]["type"] == "trigger"


def test_create_workflow_empty_name_invalid() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={"name": ""},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_workflow_name_too_long() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={"name": "x" * 256},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# get_workflow
# ---------------------------------------------------------------------------

def test_get_workflow_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post(
        "/workflows",
        json={"name": "My Flow"},
        headers={"X-API-Key": _VALID_KEY},
    )
    wf_id = cr.json()["id"]
    resp = client.get(f"/workflows/{wf_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["id"] == wf_id
    assert resp.json()["name"] == "My Flow"


def test_get_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/workflows/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# update_workflow
# ---------------------------------------------------------------------------

def test_update_workflow_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post(
        "/workflows",
        json={"name": "Original", "description": "Original desc"},
        headers={"X-API-Key": _VALID_KEY},
    )
    wf_id = cr.json()["id"]
    resp = client.put(
        f"/workflows/{wf_id}",
        json={"name": "Updated", "description": "New desc", "definition": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204
    # Verify the update by fetching the workflow (PUT returns 204 No Content)
    get_resp = client.get(f"/workflows/{wf_id}", headers={"X-API-Key": _VALID_KEY})
    body = get_resp.json()
    assert body["name"] == "Updated"
    assert body["version"] == 2  # Version bumped


def test_update_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/workflows/nonexistent",
        json={"name": "New", "description": "", "definition": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_update_workflow_empty_name_invalid() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post("/workflows", json={"name": "Flow"}, headers={"X-API-Key": _VALID_KEY})
    wf_id = cr.json()["id"]
    resp = client.put(
        f"/workflows/{wf_id}",
        json={"name": "", "description": "", "definition": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# delete_workflow
# ---------------------------------------------------------------------------

def test_delete_workflow_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post(
        "/workflows",
        json={"name": "To Delete"},
        headers={"X-API-Key": _VALID_KEY},
    )
    wf_id = cr.json()["id"]
    resp = client.delete(f"/workflows/{wf_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204
    # Confirm gone
    get_resp = client.get(f"/workflows/{wf_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 404


def test_delete_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/workflows/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

def test_run_workflow_dry_run() -> None:
    """Without GoalService, run falls back to dry_run mode."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post(
        "/workflows",
        json={"name": "Run Me", "definition": _sample_definition()},
        headers={"X-API-Key": _VALID_KEY},
    )
    wf_id = cr.json()["id"]
    resp = client.post(
        f"/workflows/{wf_id}/run",
        json={"dry_run": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "dry_run"


def test_run_workflow_with_goal_service() -> None:
    goal_svc = AsyncMock()
    goal_svc.submit_goal = AsyncMock(return_value={"goal_id": "gid-1", "status": "planning"})

    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)
    cr = client.post(
        "/workflows",
        json={"name": "Executable Flow", "definition": _sample_definition()},
        headers={"X-API-Key": _VALID_KEY},
    )
    wf_id = cr.json()["id"]
    resp = client.post(
        f"/workflows/{wf_id}/run",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body.get("status") in ("submitted", "planning", "dry_run")


def test_run_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows/nonexistent/run",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tenant isolation — different tenants can't access each other's workflows
# ---------------------------------------------------------------------------

def test_workflow_tenant_isolation() -> None:
    """Workflows from tenant A should not be visible to tenant B."""
    store = _WorkflowStore()

    async def _resolve_a(key: str) -> TenantContext | None:
        if key == "key-A":
            return TenantContext(tenant_id="tid-A", plan=PlanTier.PROFESSIONAL, api_key_id="kid-A")
        if key == "key-B":
            return TenantContext(tenant_id="tid-B", plan=PlanTier.PROFESSIONAL, api_key_id="kid-B")
        return None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve_a)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = store

    client = TestClient(app, raise_server_exceptions=False)

    # Tenant A creates a workflow
    cr = client.post(
        "/workflows",
        json={"name": "A's Flow"},
        headers={"X-API-Key": "key-A"},
    )
    wf_id = cr.json()["id"]

    # Tenant B lists — should see nothing
    list_resp = client.get("/workflows", headers={"X-API-Key": "key-B"})
    assert list_resp.json() == []

    # Tenant B cannot get A's workflow
    get_resp = client.get(f"/workflows/{wf_id}", headers={"X-API-Key": "key-B"})
    assert get_resp.status_code == 404
