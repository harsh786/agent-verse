"""WF-02: only the assignee (or a member of the assigned role) may act on a
workflow approval; an admin may override, and the override is audited.

Old bug: ``POST /approvals/{id}/decide`` (and delegate / escalate / bulk-decide)
checked nothing, so any user or API key of the tenant could approve or reject an
approval assigned to someone else, and the reviewer identity came from a
process-global ``app.state.current_user_id`` that nothing set ("anonymous").

Runs against the real in-memory ``HITLWorkflowGateway`` (no mocks) with a real
``TenantContext`` per request, as ``TenantMiddleware`` would set it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.workflow.hitl_extension import HITLWorkflowGateway, authorize_reviewer
from app.workflow.router_hitl import router

_T = "tenant-a"


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any, *, tenant_ctx: Any) -> None:
        self.events.append((event, tenant_ctx))


def _client(gateway: HITLWorkflowGateway, audit: _Audit) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        key = request.headers.get("x-key", "")
        roles = tuple(r for r in request.headers.get("x-roles", "").split(",") if r)
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id=key, roles=roles
        )
        request.app.state.hitl_workflow_gateway = gateway
        request.app.state.audit_log = audit
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _as(key: str, *roles: str) -> dict[str, str]:
    return {"x-key": key, "x-roles": ",".join(roles)}


async def _approval(
    gw: HITLWorkflowGateway, *, user: str | None = None, role: str = ""
) -> str:
    return await gw.create_workflow_approval(
        run_id="run-1",
        step_id="gate",
        tenant_id=_T,
        strategy="specific_user" if user else "round_robin",
        specific_user=user,
        assignee_role=role,
    )


@pytest.fixture
def gw() -> HITLWorkflowGateway:
    return HITLWorkflowGateway()


@pytest.mark.asyncio
async def test_other_user_cannot_decide_an_approval_assigned_to_someone_else(
    gw: HITLWorkflowGateway,
) -> None:
    rid = await _approval(gw, user="key-alice")
    client = _client(gw, _Audit())

    resp = client.post(
        f"/api/v1/approvals/{rid}/decide",
        json={"action": "approve"},
        headers=_as("key-bob", "approver"),
    )
    assert resp.status_code == 403, resp.text
    req = await gw.get_request(rid, _T)
    assert req is not None and req.status == "pending" and req.reviewed_by is None

    # Delegate / escalate / bulk-decide are gated the same way.
    assert (
        client.post(
            f"/api/v1/approvals/{rid}/delegate",
            json={"to_user_id": "key-bob"},
            headers=_as("key-bob", "approver"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/approvals/{rid}/escalate", headers=_as("key-bob", "approver")
        ).status_code
        == 403
    )
    bulk = client.post(
        "/api/v1/approvals/bulk-decide",
        json={"request_ids": [rid], "action": "approve"},
        headers=_as("key-bob", "approver"),
    )
    assert bulk.status_code == 200 and bulk.json()["decided"] == 0
    assert bulk.json()["denied"] == [rid]
    req = await gw.get_request(rid, _T)
    assert req is not None and req.status == "pending" and req.assigned_to == "key-alice"


@pytest.mark.asyncio
async def test_assignee_decides_and_is_recorded_as_reviewer(gw: HITLWorkflowGateway) -> None:
    rid = await _approval(gw, user="key-alice")
    client = _client(gw, _Audit())

    # Alice's inbox shows it as decidable; Bob's inbox does not show it at all.
    inbox = client.get("/api/v1/approvals", headers=_as("key-alice")).json()
    assert [(i["request_id"], i["can_decide"]) for i in inbox["items"]] == [(rid, True)]
    assert client.get("/api/v1/approvals", headers=_as("key-bob", "approver")).json()[
        "items"
    ] == []

    resp = client.post(
        f"/api/v1/approvals/{rid}/decide", json={"action": "approve"}, headers=_as("key-alice")
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reviewed_by"] == "key-alice"


@pytest.mark.asyncio
async def test_role_assignment_requires_the_role(gw: HITLWorkflowGateway) -> None:
    rid = await _approval(gw, role="approver")
    client = _client(gw, _Audit())

    denied = client.post(
        f"/api/v1/approvals/{rid}/decide", json={"action": "approve"}, headers=_as("k", "viewer")
    )
    assert denied.status_code == 403
    ok = client.post(
        f"/api/v1/approvals/{rid}/decide",
        json={"action": "approve"},
        headers=_as("k2", "approver"),
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_admin_override_is_allowed_and_audited(gw: HITLWorkflowGateway) -> None:
    rid = await _approval(gw, user="key-alice")
    audit = _Audit()
    client = _client(gw, audit)

    item = client.get(f"/api/v1/approvals/{rid}", headers=_as("key-root", "admin")).json()
    assert item["can_decide"] is True and item["requires_override"] is True

    resp = client.post(
        f"/api/v1/approvals/{rid}/decide",
        json={"action": "approve"},
        headers=_as("key-root", "admin"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reviewed_by"] == "key-root"
    assert len(audit.events) == 1
    event, ctx = audit.events[0]
    assert event.tool_name == "workflow.approval.admin_override"
    assert event.approver == "key-root" and event.outcome == "decide"
    assert rid in event.note and "key-alice" in event.note
    assert ctx.tenant_id == _T


def test_authorize_reviewer_policy() -> None:
    from app.workflow.hitl_extension import WorkflowHITLRequest

    mine = WorkflowHITLRequest(assigned_to="a")
    role = WorkflowHITLRequest(assigned_role="finance")
    open_ = WorkflowHITLRequest()
    assert authorize_reviewer(mine, "a", frozenset()).allowed
    assert not authorize_reviewer(mine, "b", frozenset({"approver"})).allowed
    assert not authorize_reviewer(mine, "", frozenset()).allowed
    assert authorize_reviewer(role, "x", frozenset({"finance"})).allowed
    assert not authorize_reviewer(role, "x", frozenset({"approver"})).allowed
    assert authorize_reviewer(open_, "x", frozenset({"approver"})).allowed
    assert not authorize_reviewer(open_, "x", frozenset({"operator", "viewer"})).allowed
    admin = authorize_reviewer(mine, "root", frozenset({"admin"}))
    assert admin.allowed and admin.override
