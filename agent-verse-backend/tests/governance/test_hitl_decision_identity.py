"""Regression: approval decisions must be attributed to the authenticated caller.

* ``POST /governance/hitl/batch-approve`` took ``approver`` from the request
  body (one key could approve as anyone and satisfy a multi-approver gate
  alone) and re-published the resolution as ``"approve"``/``"reject"`` -- values
  no cross-replica waiter recognises (they expect ``"approved"``/``"rejected"``).
* The org approval endpoints took ``approver`` from the body as well.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TID = "t-ident"
ALICE = TenantContext(
    tenant_id=TID, plan=PlanTier.ENTERPRISE, api_key_id="kid-alice", roles=("approver",)
)


def _gov_app(gw: HITLGateway) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ALICE if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.hitl_gateway = gw
    return app


def test_batch_approve_ignores_body_approver_and_bad_action_publish() -> None:
    gw = HITLGateway()
    gw.publish_resolution = AsyncMock()  # type: ignore[method-assign]
    ids = [
        str(gw.request_approval(goal_id="g", action=f"deploy {i}", tenant_ctx=ALICE))
        for i in range(2)
    ]
    resp = TestClient(_gov_app(gw)).post(
        "/governance/hitl/batch-approve",
        json={"action": "approve", "request_ids": ids, "approver": "mallory"},
        headers={"X-API-Key": "k"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] == 2
    for rid in ids:
        req = gw.get_request(rid, tenant_ctx=ALICE)
        assert req is not None and req.approver == "kid-alice"
        assert "mallory" not in req.approvers_list
    for call in gw.publish_resolution.await_args_list:
        assert call.kwargs.get("action") in {"approved", "rejected"}


def test_batch_reject_attributes_to_caller() -> None:
    gw = HITLGateway()
    gw.publish_resolution = AsyncMock()  # type: ignore[method-assign]
    rid = str(gw.request_approval(goal_id="g", action="deploy", tenant_ctx=ALICE))
    resp = TestClient(_gov_app(gw)).post(
        "/governance/hitl/batch-approve",
        json={"action": "reject", "request_ids": [rid], "approver": "mallory"},
        headers={"X-API-Key": "k"},
    )
    assert resp.status_code == 200, resp.text
    req = gw.get_request(rid, tenant_ctx=ALICE)
    assert req is not None and req.approver == "kid-alice"
    for call in gw.publish_resolution.await_args_list:
        assert call.kwargs.get("action") in {"approved", "rejected"}


def test_batch_approve_body_without_approver_is_accepted() -> None:
    gw = HITLGateway()
    rid = str(gw.request_approval(goal_id="g", action="deploy", tenant_ctx=ALICE))
    resp = TestClient(_gov_app(gw)).post(
        "/governance/hitl/batch-approve",
        json={"action": "approve", "request_ids": [rid]},
        headers={"X-API-Key": "k"},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Org approvals
# ---------------------------------------------------------------------------


def _org_task(org_id: str) -> MagicMock:
    t = MagicMock()
    t.org_id = org_id
    t.extra_data = {"task_kind": "approval_gate"}
    t.outputs = []
    t.mission_id = None
    return t


def test_org_approval_records_authenticated_caller_not_body() -> None:
    from app.org.router import get_org_service
    from app.org.router import router as org_router

    svc = MagicMock()
    svc._tenant_id = TID
    svc.get_task = AsyncMock(return_value=_org_task("org-1"))
    svc.update_task_status = AsyncMock(return_value=_org_task("org-1"))
    svc.get_mission = AsyncMock(return_value=None)
    svc.list_tasks = AsyncMock(return_value=[])

    app = FastAPI()
    lead = TenantContext(
        tenant_id=TID, plan=PlanTier.ENTERPRISE, api_key_id="kid-lead", roles=("admin",)
    )

    async def _resolve(key: str) -> TenantContext | None:
        return lead if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(org_router)
    app.dependency_overrides[get_org_service] = lambda: svc

    with patch("app.org.events.get_org_event_publisher", return_value=None):
        resp = TestClient(app).post(
            "/v1/org/org-1/approvals/a-1/approve",
            json={"approver": "the-ceo", "note": "ok"},
            headers={"X-API-Key": "k"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approver"] == "kid-lead"
    outputs = svc.update_task_status.await_args.kwargs["outputs"]
    assert outputs[0]["approved_by"] == "kid-lead"
