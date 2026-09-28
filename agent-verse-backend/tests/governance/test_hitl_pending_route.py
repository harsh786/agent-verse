"""Regression: GET /governance/hitl/pending (the frontend's path) is routed.

``agent-verse-frontend/src/lib/api/client.ts`` getPendingApprovals() calls
``/governance/hitl/pending``; the backend only served ``/governance/approvals``,
so the call 404'd and the approvals inbox looked empty.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import governance as gov_api
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-pend", plan=PlanTier.FREE, api_key_id="k", roles=("admin",))


def test_hitl_pending_lists_the_same_approvals_as_approvals() -> None:
    gw = HITLGateway()
    req = gw.request_approval(goal_id="g1", action="deploy prod", tenant_ctx=CTX)
    app = FastAPI()
    app.state.hitl_gateway = gw

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(gov_api.router)
    client = TestClient(app, raise_server_exceptions=False)
    pending = client.get("/governance/hitl/pending")
    assert pending.status_code == 200, pending.text
    assert pending.json() == client.get("/governance/approvals").json()
    assert any(a.get("request_id") == req.request_id for a in pending.json())
