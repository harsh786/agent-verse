"""Tests for the main workflow engine router (CRUD + publish + trigger), run
against the real ``WorkflowService`` over the in-memory workflow store and an
in-memory run store (WF-13: these used to mock the service with MagicMock, so
they proved only that the router forwarded calls)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.workflow.service import WorkflowService

_T = "test-tenant"


# ── Test app ──────────────────────────────────────────────────────────────────


def make_app(service: Any, *, runner: Any = None) -> TestClient:
    """The workflow router with a real per-request TenantContext."""
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanTier, TenantContext
    from app.workflow.router import router

    app = FastAPI()

    @app.middleware("http")
    async def inject_state(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="key-1", roles=("operator",)
        )
        request.app.state.workflow_service = service
        request.app.state.workflow_runner = runner
        request.app.state.nl_trigger_resolver = None
        return await call_next(request)

    from app.workflow.router_versions import router as versions_router

    app.include_router(router, prefix="/api/v1")
    app.include_router(versions_router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


class _MemRunStore:
    """In-memory run store: versions, permissions, analytics, webhook events."""

    def __init__(self) -> None:
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.grants: list[dict[str, Any]] = []

    async def list_versions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return list(self.versions.get(workflow_id, []))

    async def get_definition_version(
        self, tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, Any] | None:
        for v in self.versions.get(workflow_id, []):
            if str(v["version"]) == version:
                return v
        return None

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return [g for g in self.grants if g["workflow_id"] == workflow_id]

    async def add_permission(
        self, tenant_id: str, workflow_id: str, *, subject_type: str, subject_id: str,
        permission: str,
    ) -> dict[str, Any]:
        grant = {
            "id": uuid.uuid4().hex, "workflow_id": workflow_id, "subject_type": subject_type,
            "subject_id": subject_id, "permission": permission,
        }
        self.grants.append(grant)
        return grant

    async def remove_permission(self, tenant_id: str, workflow_id: str, pid: str) -> bool:
        before = len(self.grants)
        self.grants = [g for g in self.grants if g["id"] != pid]
        return len(self.grants) < before

    async def aggregate_run_stats(self, tenant_id: str, days: int) -> dict[str, Any]:
        return {"total": 3, "complete": 2, "failed": 1}

    async def workflow_run_stats(
        self, tenant_id: str, workflow_id: str, days: int
    ) -> dict[str, Any]:
        return {"total": 1, "complete": 1, "failed": 0}

    async def list_webhook_events(
        self, tenant_id: str, workflow_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        return [], 0


@pytest.fixture
def run_store() -> _MemRunStore:
    return _MemRunStore()


@pytest.fixture
def svc(run_store: _MemRunStore) -> WorkflowService:
    return WorkflowService(store=_WorkflowStore(), run_store=run_store)


@pytest.fixture
def client(svc: WorkflowService) -> TestClient:
    return make_app(svc)


def _create(client: TestClient, name: str = "My Workflow", **definition: Any) -> str:
    resp = client.post(
        "/api/v1/workflows",
        json={"name": name, "definition": {"name": name, "steps": [], **definition}},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ── CRUD ──────────────────────────────────────────────────────────────────────


def test_create_then_get_round_trips(client: TestClient) -> None:
    wid = _create(client, "Invoices", steps=[{"id": "s", "type": "transform"}])
    got = client.get(f"/api/v1/workflows/{wid}")
    assert got.status_code == 200
    body = got.json()
    assert body["name"] == "Invoices" and body["status"] == "draft"
    assert body["definition"]["steps"][0]["id"] == "s"
    assert body["access"] == "admin"  # empty ACL = tenant default


def test_create_workflow_invalid_dsl(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows", json={
        "name": "Bad WF",
        "definition": {"steps": [{"id": "a", "depends_on": ["nonexistent"]}]},
    })
    assert resp.status_code in (400, 422)


def test_list_workflows_contains_created(client: TestClient) -> None:
    wid = _create(client)
    resp = client.get("/api/v1/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1 and [i["id"] for i in data["items"]] == [wid]


def test_get_workflow_not_found(client: TestClient) -> None:
    assert client.get(f"/api/v1/workflows/{uuid.uuid4()}").status_code == 404


def test_update_workflow_persists(client: TestClient) -> None:
    wid = _create(client)
    resp = client.patch(f"/api/v1/workflows/{wid}", json={"name": "Updated"})
    assert resp.status_code == 200 and resp.json()["name"] == "Updated"
    assert client.get(f"/api/v1/workflows/{wid}").json()["name"] == "Updated"


def test_delete_archives(client: TestClient) -> None:
    wid = _create(client)
    assert client.delete(f"/api/v1/workflows/{wid}").status_code == 204
    assert client.get(f"/api/v1/workflows/{wid}").json()["status"] == "archived"


def test_delete_workflow_not_found(client: TestClient) -> None:
    assert client.delete(f"/api/v1/workflows/{uuid.uuid4()}").status_code == 404


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_publish_and_unpublish(client: TestClient) -> None:
    wid = _create(client, trigger={"type": "webhook"})
    pub = client.post(f"/api/v1/workflows/{wid}/publish")
    assert pub.status_code == 200
    assert pub.json()["status"] == "published"
    assert pub.json()["webhook_path"].startswith("/wf-hooks/")
    unpub = client.post(f"/api/v1/workflows/{wid}/unpublish")
    assert unpub.status_code == 200 and unpub.json()["status"] == "draft"


def test_publish_not_found(client: TestClient) -> None:
    assert client.post(f"/api/v1/workflows/{uuid.uuid4()}/publish").status_code == 404


# ── Validation / trigger ──────────────────────────────────────────────────────


def test_validate_workflow_valid(client: TestClient) -> None:
    wid = _create(client)
    resp = client.post(f"/api/v1/workflows/{wid}/validate")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "errors": []}


def test_validate_workflow_not_found(client: TestClient) -> None:
    assert client.post(f"/api/v1/workflows/{uuid.uuid4()}/validate").status_code == 404


def test_trigger_without_runner_is_503(client: TestClient) -> None:
    wid = _create(client)
    resp = client.post(f"/api/v1/workflows/{wid}/trigger", json={"inputs": {}})
    assert resp.status_code == 503


def test_nl_trigger_preview_no_resolver(client: TestClient) -> None:
    resp = client.post("/api/v1/workflows/nl-trigger-preview", json={"description": "every minute"})
    assert resp.status_code == 503  # resolver not configured


# ── Versions / analytics / webhook events ─────────────────────────────────────


def test_list_versions_reads_the_run_store(client: TestClient, run_store: _MemRunStore) -> None:
    wid = _create(client)
    run_store.versions[wid] = [{"version": "1.0.0", "definition_json": {"steps": []}}]
    resp = client.get(f"/api/v1/workflows/{wid}/versions")
    assert resp.status_code == 200
    assert [v["version"] for v in resp.json()] == ["1.0.0"]


def test_analytics(client: TestClient) -> None:
    wid = _create(client)
    summary = client.get("/api/v1/workflows/analytics/summary")
    assert summary.status_code == 200
    per_wf = client.get(f"/api/v1/workflows/{wid}/analytics")
    assert per_wf.status_code == 200


def test_webhook_events_empty(client: TestClient) -> None:
    wid = _create(client)
    resp = client.get(f"/api/v1/workflows/{wid}/webhooks")
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["total"] == 0


# ── Permissions ───────────────────────────────────────────────────────────────


def test_permissions_add_list_remove(client: TestClient) -> None:
    wid = _create(client)
    assert client.get(f"/api/v1/workflows/{wid}/permissions").json() == []
    added = client.post(
        f"/api/v1/workflows/{wid}/permissions", json={"subject": "key-1", "role": "admin"}
    )
    assert added.status_code == 201
    grants = client.get(f"/api/v1/workflows/{wid}/permissions").json()
    assert [(g["subject_id"], g["permission"]) for g in grants] == [("key-1", "admin")]
    pid = added.json()["id"]
    assert client.delete(f"/api/v1/workflows/{wid}/permissions/{pid}").status_code == 204
    assert client.get(f"/api/v1/workflows/{wid}/permissions").json() == []


def test_remove_unknown_permission_is_404(client: TestClient) -> None:
    wid = _create(client)
    assert client.delete(f"/api/v1/workflows/{wid}/permissions/nope").status_code == 404


# ── Route table ───────────────────────────────────────────────────────────────


def test_every_workflow_path_has_exactly_one_handler() -> None:
    """GET /workflows/{id}/versions (and its restore) used to be declared in both
    router.py and router_versions.py; the second copy was unreachable."""
    from collections import Counter

    from fastapi.routing import APIRoute

    from app.workflow.router import router as main_router
    from app.workflow.router_runs import router as runs_router
    from app.workflow.router_versions import router as versions_router

    routes = [
        route
        for r in (main_router, versions_router, runs_router)
        for route in r.routes
        if isinstance(route, APIRoute)
    ]
    seen = Counter((method, route.path) for route in routes for method in route.methods)
    assert ("GET", "/workflows/{workflow_id}/versions") in seen
    dupes = {k: n for k, n in seen.items() if n > 1}
    assert dupes == {}
