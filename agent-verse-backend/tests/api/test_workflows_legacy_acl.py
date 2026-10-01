"""LEGACY-WF-ROUTER: the legacy /workflows routes enforce the per-workflow ACL.

The visual builder still saves and runs through ``PUT /workflows/{id}`` and
``POST /workflows/{id}/run`` (and ``DELETE /workflows/{id}``), so they cannot be
retired. They skipped the per-workflow ACL that ``/api/v1/workflows`` enforces
(a 'viewer' grant restricted nothing there) and the pending-approval edit
freeze (a definition could be swapped under a submitted publish request).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.api.workflows import router as workflows_router
from app.tenancy.context import PlanTier, TenantContext

_T = "tid-wf"


class _AclService:
    """Just the ACL surface ``app.workflow.permissions`` reads."""

    def __init__(self) -> None:
        self.grants: dict[str, list[dict[str, Any]]] = {}

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return list(self.grants.get(workflow_id, []))


def _client() -> tuple[TestClient, _WorkflowStore, _AclService]:
    app = FastAPI()
    store = _WorkflowStore()
    acl = _AclService()

    @app.middleware("http")
    async def inject(request: Request, call_next: Any) -> Any:
        key = request.headers.get("x-key", "owner")
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.PROFESSIONAL, api_key_id=key, roles=("operator",)
        )
        return await call_next(request)

    app.include_router(workflows_router)
    app.state.workflow_store = store
    app.state.workflow_service = acl
    return TestClient(app), store, acl


def _grant(acl: _AclService, wid: str, **levels: str) -> None:
    acl.grants[wid] = [
        {"subject_type": "user", "subject_id": who, "permission": level}
        for who, level in levels.items()
    ]


def _create(client: TestClient) -> str:
    resp = client.post("/workflows", json={"name": "wf", "definition": {"steps": []}})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


_BODY = {"name": "wf2", "definition": {"steps": []}}


def test_viewer_cannot_update_delete_or_run() -> None:
    client, _, acl = _client()
    wid = _create(client)
    _grant(acl, wid, owner="admin", viewer="viewer")
    as_viewer = {"x-key": "viewer"}

    assert client.get(f"/workflows/{wid}", headers=as_viewer).status_code == 200
    assert client.put(f"/workflows/{wid}", json=_BODY, headers=as_viewer).status_code == 403
    assert client.post(f"/workflows/{wid}/run", headers=as_viewer).status_code == 403
    assert client.delete(f"/workflows/{wid}", headers=as_viewer).status_code == 403
    # Someone with no grant at all cannot even read it.
    assert client.get(f"/workflows/{wid}", headers={"x-key": "stranger"}).status_code == 403


def test_runner_can_run_but_not_edit_and_editor_can_edit() -> None:
    client, _, acl = _client()
    wid = _create(client)
    _grant(acl, wid, owner="admin", runner="runner", editor="editor")

    dry = client.post(f"/workflows/{wid}/run?dry_run=true", headers={"x-key": "runner"})
    assert dry.status_code == 202, dry.text
    assert client.put(f"/workflows/{wid}", json=_BODY, headers={"x-key": "runner"}).status_code == 403
    assert client.put(f"/workflows/{wid}", json=_BODY, headers={"x-key": "editor"}).status_code == 204


def test_empty_acl_keeps_the_tenant_default() -> None:
    client, _, _ = _client()
    wid = _create(client)
    assert client.put(f"/workflows/{wid}", json=_BODY).status_code == 204


def test_edit_is_refused_while_pending_publish_approval() -> None:
    import asyncio

    client, store, _ = _client()
    wid = _create(client)
    asyncio.run(store.update(tenant_id=_T, workflow_id=wid, status="pending_approval"))
    resp = client.put(f"/workflows/{wid}", json=_BODY)
    assert resp.status_code == 409, resp.text
    assert "pending" in resp.json()["detail"]
