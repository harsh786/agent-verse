"""HITL-07: a below-quorum vote is not reported as 'approved'.

On a multi-approver gate ``approve_async`` returns True once a vote is recorded,
and the REST route answered ``{"status": "approved"}`` while the gate stayed
closed. It now answers ``vote_recorded`` with the count until quorum.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as gov_router
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEYS = {"k-alice": "kid-alice", "k-bob": "kid-bob"}


def _app(gw: HITLGateway) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> Any:
        if key not in _KEYS:
            return None
        return TenantContext(
            tenant_id="t-quorum",
            plan=PlanTier.ENTERPRISE,
            api_key_id=_KEYS[key],
            roles=("approver",),
        )

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(gov_router)
    app.state.hitl_gateway = gw
    return app


def test_votes_below_quorum_are_reported_as_recorded_not_approved() -> None:
    gw = HITLGateway()
    ctx = TenantContext(tenant_id="t-quorum", plan=PlanTier.ENTERPRISE, api_key_id="raiser")
    rid = str(
        gw.request_approval(goal_id="g", action="deploy", tenant_ctx=ctx, required_approvers=2)
    )
    client = TestClient(_app(gw))

    first = client.post(
        f"/governance/approvals/{rid}/approve", json={"approver": "ignored"}, headers={"X-API-Key": "k-alice"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "vote_recorded"
    assert first.json()["required_approvers"] == 2

    second = client.post(
        f"/governance/approvals/{rid}/approve", json={"approver": "ignored"}, headers={"X-API-Key": "k-bob"}
    )
    assert second.json()["status"] == "approved"
