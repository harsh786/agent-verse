"""Tests for /workflows endpoints — CRUD + run + tenant isolation."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.api.workflows import router as workflows_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ── Test fixtures ─────────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="tid-wf", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_test_workflows123"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(goal_service: object | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the workflows router and in-memory store."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = _WorkflowStore()
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


# ── List ──────────────────────────────────────────────────────────────────────

def test_list_workflows_empty_initially() -> None:
    client = TestClient(_make_app())
    resp = client.get("/workflows", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_workflows_returns_all_tenant_workflows() -> None:
    app = _make_app()
    client = TestClient(app)
    client.post("/workflows", json={"name": "Flow 1"}, headers=_HEADERS)
    client.post("/workflows", json={"name": "Flow 2"}, headers=_HEADERS)

    resp = client.get("/workflows", headers=_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    names = {w["name"] for w in resp.json()}
    assert names == {"Flow 1", "Flow 2"}


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_workflow_returns_201() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/workflows",
        json={
            "name": "My Workflow",
            "description": "A test workflow",
            "definition": {"nodes": [], "edges": []},
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Workflow"
    assert data["description"] == "A test workflow"
    assert data["status"] == "draft"
    assert data["version"] == 1
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_create_workflow_name_required() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/workflows",
        json={"description": "missing name"},
        headers=_HEADERS,
    )
    assert resp.status_code == 422


def test_create_workflow_defaults_empty_definition() -> None:
    client = TestClient(_make_app())
    resp = client.post("/workflows", json={"name": "Minimal"}, headers=_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["definition"] == {}


# ── Get ───────────────────────────────────────────────────────────────────────

def test_get_workflow_returns_200() -> None:
    app = _make_app()
    client = TestClient(app)
    wf_id = client.post(
        "/workflows", json={"name": "Fetch Me"}, headers=_HEADERS
    ).json()["id"]

    resp = client.get(f"/workflows/{wf_id}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["id"] == wf_id
    assert resp.json()["name"] == "Fetch Me"


def test_get_workflow_not_found_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.get("/workflows/does-not-exist", headers=_HEADERS)
    assert resp.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

def test_update_workflow_returns_204_and_bumps_version() -> None:
    app = _make_app()
    client = TestClient(app)
    wf_id = client.post(
        "/workflows", json={"name": "Original"}, headers=_HEADERS
    ).json()["id"]

    resp = client.put(
        f"/workflows/{wf_id}",
        json={
            "name": "Updated",
            "description": "new description",
            "definition": {"nodes": [{"id": "n1"}]},
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 204

    fetched = client.get(f"/workflows/{wf_id}", headers=_HEADERS).json()
    assert fetched["name"] == "Updated"
    assert fetched["description"] == "new description"
    assert fetched["version"] == 2
    assert fetched["definition"]["nodes"] == [{"id": "n1"}]


def test_update_nonexistent_workflow_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.put(
        "/workflows/ghost-id",
        json={"name": "X", "description": "", "definition": {}},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_workflow_returns_204() -> None:
    app = _make_app()
    client = TestClient(app)
    wf_id = client.post(
        "/workflows", json={"name": "Doomed"}, headers=_HEADERS
    ).json()["id"]

    assert client.delete(f"/workflows/{wf_id}", headers=_HEADERS).status_code == 204
    assert client.get(f"/workflows/{wf_id}", headers=_HEADERS).status_code == 404


def test_delete_nonexistent_workflow_returns_404() -> None:
    client = TestClient(_make_app())
    assert (
        client.delete("/workflows/no-such-id", headers=_HEADERS).status_code == 404
    )


# ── Run ───────────────────────────────────────────────────────────────────────

def test_run_workflow_dry_run_returns_202() -> None:
    app = _make_app()
    client = TestClient(app)
    wf_id = client.post(
        "/workflows",
        json={"name": "Dry Flow", "description": "test"},
        headers=_HEADERS,
    ).json()["id"]

    resp = client.post(f"/workflows/{wf_id}/run?dry_run=true", headers=_HEADERS)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "dry_run"
    assert data["workflow_id"] == wf_id
    assert "goal" in data


def test_run_workflow_without_durable_engine_is_503() -> None:
    # Legacy /run now uses the durable engine path (see
    # tests/api/test_workflow_legacy_run_durable.py). This harness wires no
    # durable runner, so the honest answer is 503 and there is NO goal-submission
    # fallback (the old silent-success bug).
    client = TestClient(_make_app())
    wf_id = client.post(
        "/workflows", json={"name": "No Service Flow"}, headers=_HEADERS
    ).json()["id"]
    resp = client.post(f"/workflows/{wf_id}/run", headers=_HEADERS)
    assert resp.status_code == 503


def test_run_workflow_never_falls_back_to_goal_service() -> None:
    # Legacy /run now uses the durable engine path (see
    # tests/api/test_workflow_legacy_run_durable.py). This harness wires no
    # durable runner, so the honest answer is 503 and there is NO goal-submission
    # fallback (the old silent-success bug).
    mock_svc = AsyncMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    wf_id = client.post(
        "/workflows", json={"name": "Live Flow"}, headers=_HEADERS
    ).json()["id"]
    resp = client.post(f"/workflows/{wf_id}/run", headers=_HEADERS)
    assert resp.status_code == 503
    mock_svc.submit_goal.assert_not_awaited()


def test_run_nonexistent_workflow_returns_404() -> None:
    client = TestClient(_make_app())
    resp = client.post("/workflows/nonexistent/run", headers=_HEADERS)
    assert resp.status_code == 404


def test_run_workflow_dry_run_goal_includes_workflow_name() -> None:
    """The dry-run preview still describes the workflow by name."""
    client = TestClient(_make_app())
    wf_id = client.post(
        "/workflows", json={"name": "Complex Flow"}, headers=_HEADERS
    ).json()["id"]
    resp = client.post(f"/workflows/{wf_id}/run?dry_run=true", headers=_HEADERS)
    assert "Complex Flow" in resp.json()["goal"]


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_unauthorized_request_returns_401() -> None:
    client = TestClient(_make_app())
    resp = client.get("/workflows", headers={"X-API-Key": "bad-key"})
    assert resp.status_code == 401


def test_missing_api_key_returns_401() -> None:
    client = TestClient(_make_app())
    resp = client.get("/workflows")
    assert resp.status_code == 401


# ── Tenant isolation ──────────────────────────────────────────────────────────

def test_tenant_isolation_prevents_cross_tenant_access() -> None:
    """Tenant A cannot read, update, delete, or run Tenant B's workflows."""
    ctx_a = TenantContext(tenant_id="tenant-a", plan=PlanTier.FREE, api_key_id="ka")
    ctx_b = TenantContext(tenant_id="tenant-b", plan=PlanTier.FREE, api_key_id="kb")

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == "key-a":
            return ctx_a
        if key == "key-b":
            return ctx_b
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = _WorkflowStore()

    client = TestClient(app)
    headers_a = {"X-API-Key": "key-a"}
    headers_b = {"X-API-Key": "key-b"}

    # Tenant A creates a workflow
    wf_id = client.post(
        "/workflows", json={"name": "Tenant A Flow"}, headers=headers_a
    ).json()["id"]

    # Tenant B cannot get it
    assert client.get(f"/workflows/{wf_id}", headers=headers_b).status_code == 404

    # Tenant B's list is empty
    assert client.get("/workflows", headers=headers_b).json() == []

    # Tenant B cannot delete it
    assert (
        client.delete(f"/workflows/{wf_id}", headers=headers_b).status_code == 404
    )

    # Tenant B cannot update it
    assert (
        client.put(
            f"/workflows/{wf_id}",
            json={"name": "Stolen", "description": "", "definition": {}},
            headers=headers_b,
        ).status_code
        == 404
    )

    # But Tenant A still owns it
    assert (
        client.get(f"/workflows/{wf_id}", headers=headers_a).status_code == 200
    )
