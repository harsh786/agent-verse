"""WF-03: two reviewers deciding one approval — exactly one wins and resumes.

Old bug: decide() checked status == 'pending' and then upserted
unconditionally, so a concurrent approve and reject both passed, the last
write won, and both resume callbacks dispatched the run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.workflow.hitl_extension import ApprovalAlreadyDecidedError, HITLWorkflowGateway
from app.workflow.router_hitl import router

_T = "tenant-a"


class _SlowStore:
    """Durable-store stand-in whose conditional update yields mid-flight (so the
    two decisions interleave like two replicas) but stays atomic, like SQL."""

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def save(self, req: Any) -> None:
        self.rows[req.request_id] = req

    async def get(self, request_id: str, tenant_id: str | None = None) -> Any:
        return self.rows.get(request_id)

    async def decide_if_pending(self, req: Any) -> bool:
        await asyncio.sleep(0.01)
        current = self.rows.get(req.request_id)
        if current is None or current.status != "pending":
            return False
        self.rows[req.request_id] = req
        return True


async def _setup(store: Any | None) -> tuple[list[HITLWorkflowGateway], list[str], str]:
    resumed: list[str] = []

    async def resume(req: Any) -> None:
        resumed.append(req.action_taken)

    replicas = [
        HITLWorkflowGateway(approval_store=store, resume_callback=resume) for _ in range(2)
    ]
    rid = await replicas[0].create_workflow_approval(run_id="r", step_id="gate", tenant_id=_T)
    return replicas, resumed, rid


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [True, False])
async def test_concurrent_approve_and_reject_resume_once(durable: bool) -> None:
    store = _SlowStore() if durable else None
    replicas, resumed, rid = await _setup(store)
    gw_b = replicas[1] if durable else replicas[0]  # in-memory: one process

    results = await asyncio.gather(
        replicas[0].decide(rid, "approve", "alice", tenant_id=_T),
        gw_b.decide(rid, "reject", "bob", tenant_id=_T),
        return_exceptions=True,
    )

    wins = [r for r in results if not isinstance(r, BaseException)]
    losses = [r for r in results if isinstance(r, ApprovalAlreadyDecidedError)]
    assert len(wins) == 1 and len(losses) == 1, results
    assert resumed == [wins[0].action_taken]
    # The loser is told what was actually recorded.
    assert losses[0].request.reviewed_by == wins[0].reviewed_by
    final = await replicas[0].get_request(rid, _T)
    assert final is not None and final.action_taken == wins[0].action_taken


@pytest.mark.asyncio
async def test_repeating_your_own_decision_is_idempotent() -> None:
    replicas, resumed, rid = await _setup(_SlowStore())
    await replicas[0].decide(rid, "approve", "alice", tenant_id=_T)
    again = await replicas[1].decide(rid, "approve", "alice", tenant_id=_T)
    assert again.action_taken == "approve"
    assert resumed == ["approve"]
    with pytest.raises(ApprovalAlreadyDecidedError):
        await replicas[1].decide(rid, "reject", "bob", tenant_id=_T)


@pytest.mark.asyncio
async def test_a_decision_that_cannot_be_recorded_does_not_resume() -> None:
    store = _SlowStore()
    replicas, resumed, rid = await _setup(store)

    async def boom(req: Any) -> bool:
        raise ConnectionError("db down")

    store.decide_if_pending = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="could not be recorded"):
        await replicas[0].decide(rid, "approve", "alice", tenant_id=_T)
    assert resumed == []


@pytest.mark.asyncio
async def test_loser_gets_409_with_the_recorded_decision() -> None:
    gw = HITLWorkflowGateway()
    rid = await gw.create_workflow_approval(run_id="r", step_id="gate", tenant_id=_T)
    await gw.decide(rid, "approve", "key-alice", tenant_id=_T)

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=_T, plan=PlanTier.FREE, api_key_id="key-bob", roles=("approver",)
        )
        request.app.state.hitl_workflow_gateway = gw
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    resp = TestClient(app).post(f"/api/v1/approvals/{rid}/decide", json={"action": "reject"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reviewed_by"] == "key-alice" and detail["action_taken"] == "approve"
    assert "Already decided by key-alice" in detail["message"]
