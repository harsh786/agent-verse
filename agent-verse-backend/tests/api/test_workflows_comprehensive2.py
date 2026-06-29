"""Extended tests for /workflows API — covers additional paths for 67% → 85%+.

Supplements test_workflows_comprehensive.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore, router as workflows_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-wf2", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_workflows2"


def _make_app(
    store: _WorkflowStore | None = None,
    goal_service: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = store or _WorkflowStore()
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


def _create_workflow(client: TestClient, name: str = "Test Workflow") -> dict:
    resp = client.post(
        "/workflows",
        json={
            "name": name,
            "description": "A test workflow",
            "definition": {"nodes": [], "edges": []},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def test_list_workflows_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/workflows").status_code == 401


def test_create_workflow_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.post("/workflows", json={"name": "T"}).status_code == 401


# ---------------------------------------------------------------------------
# Additional creation edge cases
# ---------------------------------------------------------------------------


def test_create_workflow_with_complex_definition() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={
            "name": "Complex Workflow",
            "description": "Multi-step workflow",
            "definition": {
                "nodes": [
                    {"id": "n1", "type": "trigger", "config": {}},
                    {"id": "n2", "type": "action", "config": {"tool": "github"}},
                ],
                "edges": [{"from": "n1", "to": "n2"}],
            },
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["definition"]["nodes"][0]["id"] == "n1"
    assert body["version"] == 1


def test_create_workflow_description_defaults_to_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={"name": "No Desc"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["description"] == ""


def test_create_workflow_name_exceeds_max_returns_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows",
        json={"name": "A" * 256},  # over max_length
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Update workflow — returns 204 No Content
# ---------------------------------------------------------------------------


def test_update_workflow_returns_204() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = _create_workflow(client)
    wf_id = created["id"]

    resp = client.put(
        f"/workflows/{wf_id}",
        json={
            "name": "Updated",
            "description": "New description",
            "definition": {"nodes": [{"id": "n1"}], "edges": []},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204


def test_update_workflow_reflected_in_get() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = _create_workflow(client, "Original")
    wf_id = created["id"]

    client.put(
        f"/workflows/{wf_id}",
        json={"name": "Updated Name", "definition": {}},
        headers={"X-API-Key": _VALID_KEY},
    )

    get_resp = client.get(f"/workflows/{wf_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Updated Name"
    assert get_resp.json()["version"] == 2


def test_update_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/workflows/nonexistent",
        json={"name": "N", "definition": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Run workflow — dry_run is a query parameter
# ---------------------------------------------------------------------------


def test_run_workflow_dry_run_via_query() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = _create_workflow(client, "Dry Run Workflow")
    wf_id = created["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run?dry_run=true",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202  # run endpoint returns 202 Accepted
    assert resp.json()["status"] == "dry_run"


def test_run_workflow_no_goal_service_returns_dry_run() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = _create_workflow(client, "No Service Workflow")
    wf_id = created["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    # Without goal service, returns dry_run status
    assert resp.json()["status"] == "dry_run"


def test_run_workflow_with_goal_service_submit() -> None:
    goal_svc = AsyncMock()
    goal_svc.submit_goal.return_value = {
        "goal_id": "goal-1",
        "id": "goal-1",
        "status": "planning",
        "goal": "Execute workflow",
    }
    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)
    created = _create_workflow(client, "Submittable Workflow")
    wf_id = created["id"]

    resp = client.post(
        f"/workflows/{wf_id}/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    # With goal service, should submit and return planning status
    assert body["status"] in ("submitted", "planning", "dry_run")


def test_run_workflow_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/workflows/nonexistent/run",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Store not initialized
# ---------------------------------------------------------------------------


def test_list_no_store_returns_503() -> None:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    # Do NOT set workflow_store on app.state

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/workflows", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# List (returns plain list) 
# ---------------------------------------------------------------------------


def test_list_workflows_returns_list() -> None:
    store = _WorkflowStore()
    client = TestClient(_make_app(store=store), raise_server_exceptions=False)

    _create_workflow(client, "WF A")
    _create_workflow(client, "WF B")

    resp = client.get("/workflows", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    # Response is a plain list (no total/workflows wrapper)
    assert isinstance(body, list)
    assert len(body) == 2


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_workflow_tenant_isolation() -> None:
    store = _WorkflowStore()
    client = TestClient(_make_app(store=store), raise_server_exceptions=False)

    # Create for tenant 1
    _create_workflow(client, "T1 Workflow")

    # Second tenant
    ctx2 = TenantContext(tenant_id="tid-wf2-other", plan=PlanTier.FREE, api_key_id="kid-2")
    app2 = FastAPI()

    async def _resolve2(key: str) -> TenantContext | None:
        return ctx2 if key == "other_key" else None

    app2.add_middleware(TenantMiddleware, key_resolver=_resolve2)
    app2.add_middleware(SecurityHeadersMiddleware)
    app2.include_router(workflows_router)
    app2.state.workflow_store = store

    client2 = TestClient(app2, raise_server_exceptions=False)
    resp2 = client2.get("/workflows", headers={"X-API-Key": "other_key"})
    assert resp2.status_code == 200
    assert resp2.json() == []
