"""WF-05: per-workflow permissions are enforced, not just stored.

Old bug: ``workflow_permissions`` grants were listed by the API but never read,
so a "viewer" grantee (or anyone else in the tenant) could edit, publish,
trigger or delete the workflow.

Runs the real routers against the real in-memory workflow store and a run store
that keeps the ACL, with a real ``TenantContext`` per request.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.permissions import access_level
from app.workflow.router import router as wf_router
from app.workflow.router_runs import router as runs_router
from app.workflow.service import WorkflowService

_T = "tenant-a"


class _AclRunStore:
    """The ACL + run lookups WorkflowService reads (workflow_permissions)."""

    def __init__(self) -> None:
        self.grants: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return [g for g in self.grants if g["workflow_id"] == workflow_id]

    async def add_permission(
        self, tenant_id: str, workflow_id: str, *, subject_type: str, subject_id: str,
        permission: str,
    ) -> dict[str, Any]:
        grant = {
            "id": uuid.uuid4().hex,
            "workflow_id": workflow_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "permission": permission,
        }
        self.grants.append(grant)
        return grant

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def update_status(self, run_id: str, status: Any, **kw: Any) -> bool:
        self.runs[run_id]["status"] = str(getattr(status, "value", status))
        return True

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        run = self.runs.get(run_id)
        return run["status"] if run else None


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any, *, tenant_ctx: Any) -> None:
        self.events.append(event)


@pytest.fixture
def env() -> dict[str, Any]:
    run_store = _AclRunStore()
    svc = WorkflowService(store=_WorkflowStore(), run_store=run_store)
    audit = _Audit()
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        roles = tuple(r for r in request.headers.get("x-roles", "").split(",") if r)
        request.state.tenant = TenantContext(
            tenant_id=_T,
            plan=PlanTier.FREE,
            api_key_id=request.headers.get("x-key", ""),
            roles=roles,
        )
        request.app.state.workflow_service = svc
        request.app.state.audit_log = audit
        return await call_next(request)

    app.include_router(wf_router, prefix="/api/v1")
    app.include_router(runs_router, prefix="/api/v1")
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "svc": svc,
        "run_store": run_store,
        "audit": audit,
    }


def _as(key: str, *roles: str) -> dict[str, str]:
    return {"x-key": key, "x-roles": ",".join(roles)}


async def _workflow(svc: WorkflowService) -> str:
    wf = await svc.create(tenant_id=_T, name="wf", definition={"name": "wf", "steps": []})
    return str(wf["id"])


@pytest.mark.asyncio
async def test_viewer_grantee_can_read_but_not_change_publish_or_trigger(
    env: dict[str, Any],
) -> None:
    svc, client, store = env["svc"], env["client"], env["run_store"]
    wid = await _workflow(svc)
    await store.add_permission(
        _T, wid, subject_type="user", subject_id="key-v", permission="viewer"
    )
    viewer = _as("key-v", "operator")

    got = client.get(f"/api/v1/workflows/{wid}", headers=viewer)
    assert got.status_code == 200, got.text
    assert got.json()["access"] == "viewer"

    assert client.patch(
        f"/api/v1/workflows/{wid}", json={"name": "hacked"}, headers=viewer
    ).status_code == 403
    assert client.post(f"/api/v1/workflows/{wid}/publish", headers=viewer).status_code == 403
    assert client.post(
        f"/api/v1/workflows/{wid}/trigger", json={"inputs": {}}, headers=viewer
    ).status_code == 403
    assert client.delete(f"/api/v1/workflows/{wid}", headers=viewer).status_code == 403
    assert client.post(
        f"/api/v1/workflows/{wid}/permissions",
        json={"subject": "key-v", "role": "admin"},
        headers=viewer,
    ).status_code == 403

    item = await svc.get(_T, wid)
    assert item is not None and item["name"] == "wf" and item["status"] == "draft"
    assert env["audit"].events and env["audit"].events[0].tool_name == "workflow.access_denied"


@pytest.mark.asyncio
async def test_callers_without_a_grant_are_refused_once_an_acl_exists(
    env: dict[str, Any],
) -> None:
    svc, client, store = env["svc"], env["client"], env["run_store"]
    wid = await _workflow(svc)
    # Empty ACL = tenant-wide default: anyone in the tenant may edit.
    assert client.patch(
        f"/api/v1/workflows/{wid}", json={"name": "v2"}, headers=_as("key-x", "operator")
    ).status_code == 200

    await store.add_permission(
        _T, wid, subject_type="user", subject_id="key-e", permission="editor"
    )
    assert client.get(f"/api/v1/workflows/{wid}", headers=_as("key-x")).status_code == 403
    assert client.patch(
        f"/api/v1/workflows/{wid}", json={"name": "v3"}, headers=_as("key-e")
    ).status_code == 200
    # A tenant admin always passes (no lock-out).
    got = client.get(f"/api/v1/workflows/{wid}", headers=_as("key-root", "admin"))
    assert got.status_code == 200 and got.json()["access"] == "admin"


@pytest.mark.asyncio
async def test_run_control_needs_runner_on_the_runs_workflow(env: dict[str, Any]) -> None:
    svc, client, store = env["svc"], env["client"], env["run_store"]
    wid = await _workflow(svc)
    store.runs["run-1"] = {"run_id": "run-1", "workflow_id": wid, "status": "running"}
    await store.add_permission(
        _T, wid, subject_type="user", subject_id="key-v", permission="viewer"
    )
    await store.add_permission(
        _T, wid, subject_type="role", subject_id="operator", permission="runner"
    )

    assert client.post("/api/v1/runs/run-1/cancel", headers=_as("key-v")).status_code == 403
    assert store.runs["run-1"]["status"] == "running"
    ok = client.post("/api/v1/runs/run-1/cancel", headers=_as("key-o", "operator"))
    assert ok.status_code == 202, ok.text


def test_access_level_policy() -> None:
    acl = [
        {"subject_type": "user", "subject_id": "a", "permission": "viewer"},
        {"subject_type": "user", "subject_id": "a", "permission": "runner"},
        {"subject_type": "role", "subject_id": "finance", "permission": "editor"},
    ]
    assert access_level([], "anyone", frozenset()) == "admin"
    assert access_level(acl, "a", frozenset()) == "runner"
    assert access_level(acl, "b", frozenset({"finance"})) == "editor"
    assert access_level(acl, "b", frozenset({"operator"})) is None
    assert access_level(acl, "", frozenset()) is None
    assert access_level(acl, "b", frozenset({"admin"})) == "admin"
