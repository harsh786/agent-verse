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
        f"/governance/approvals/{rid}/approve",
        json={"approver": "ignored"},
        headers={"X-API-Key": "k-alice"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "vote_recorded"
    assert first.json()["required_approvers"] == 2

    second = client.post(
        f"/governance/approvals/{rid}/approve",
        json={"approver": "ignored"},
        headers={"X-API-Key": "k-bob"},
    )
    assert second.json()["status"] == "approved"


class _OtherReplicaGateway(HITLGateway):
    """A DB-backed gateway on a replica that did not raise (or cache) the gate.

    The approval row lives in "Postgres" (``_db_fetch_request``) and votes are
    counted there (``_db_cast_vote``); this process's cache never holds it.
    """

    def __init__(self) -> None:
        super().__init__(db_session_factory=object())
        self.votes: set[str] = set()
        self.resolved_as: str | None = None

    async def _db_fetch_request(self, request_id: str, tenant_id: str) -> Any:
        return {
            "id": request_id,
            "tenant_id": tenant_id,
            "goal_id": "g",
            "action": "deploy",
            "risk_level": "high",
            "status": self.resolved_as or "pending",
            "required_approvers": 2,
        }

    async def _db_cast_vote(self, request_id: str, tenant_id: str, approver: str, note: str) -> int:
        self.votes.add(approver)
        return len(self.votes)

    async def _db_update_resolution(self, *args: Any, **kwargs: Any) -> bool:
        self.resolved_as = "approved"
        return True

    async def _publish_approved(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_below_quorum_vote_on_a_replica_that_never_cached_the_gate() -> None:
    """a03-F056-07: the outcome came from the process-local cache, which never
    holds a worker-raised gate, so the first of two votes answered 'approved'."""
    gw = _OtherReplicaGateway()
    client = TestClient(_app(gw))
    first = client.post(
        "/governance/approvals/req-remote/approve",
        json={"approver": "ignored"},
        headers={"X-API-Key": "k-alice"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "vote_recorded"
    assert body["approvals_received"] == 1
    assert body["required_approvers"] == 2
    assert gw.resolved_as is None

    second = client.post(
        "/governance/approvals/req-remote/approve",
        json={"approver": "ignored"},
        headers={"X-API-Key": "k-bob"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert gw.resolved_as == "approved"
