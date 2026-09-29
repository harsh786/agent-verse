"""HITL correctness across replicas.

Regressions:
* ``HITLGateway._redis`` was never set in production (the lifespan only bound
  the DB), so ``hitl.approved`` / ``hitl.rejected`` triggers, cross-replica
  BLPOP delivery and rejection notes never fired, and the goal service's
  rejection subscriber was never started;
* multi-approver vote counts lived in one process's memory (and the threshold
  was never persisted), so votes split across replicas never added up;
* the sync ``approve()`` used by Slack buttons / org tasks / email links
  released the waiting agent before the DB write and only saw local requests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway, wire_hitl_runtime
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-mr", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _SharedApprovalDB:
    """In-memory stand-in for approval_requests + approval_votes (one tenant scope)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.votes: dict[tuple[str, str], set[str]] = {}
        self.resolutions: list[tuple[str, str]] = []

    def bind(self, gw: HITLGateway) -> HITLGateway:
        db = self

        async def _persist(req: Any, tenant_id: str) -> None:
            db.rows[(tenant_id, req.request_id)] = {
                "id": req.request_id, "tenant_id": tenant_id, "goal_id": req.goal_id,
                "action": req.action, "risk_level": req.risk_level, "status": "pending",
                "required_approvers": req.required_approvers,
            }

        async def _fetch(request_id: str, tenant_id: str) -> Any:
            row = db.rows.get((tenant_id, request_id))
            return dict(row) if row else None

        async def _vote(request_id: str, tenant_id: str, approver: str, note: str) -> int:
            voters = db.votes.setdefault((tenant_id, request_id), set())
            voters.add(approver)
            return len(voters)

        async def _resolve(
            request_id: str, tenant_id: str, status: str, approver: str = "", note: str = ""
        ) -> bool:
            row = db.rows.get((tenant_id, request_id))
            if row is None or row["status"] != "pending":
                return False
            row["status"] = status
            db.resolutions.append((request_id, status))
            return True

        gw._db_persist_approval_request = _persist  # type: ignore[method-assign]
        gw._db_fetch_request = _fetch  # type: ignore[method-assign]
        gw._db_cast_vote = _vote  # type: ignore[method-assign]
        gw._db_update_resolution = _resolve  # type: ignore[method-assign]
        return gw


def _pair() -> tuple[_SharedApprovalDB, HITLGateway, HITLGateway]:
    db = _SharedApprovalDB()
    a = db.bind(HITLGateway(db_session_factory=object()))
    b = db.bind(HITLGateway(db_session_factory=object()))
    return db, a, b


@pytest.mark.asyncio
async def test_multi_approver_votes_add_up_across_replicas() -> None:
    db, replica_a, replica_b = _pair()
    request_id = await replica_a.request_approval_async(
        goal_id="g1", action="deploy prod", tenant_ctx=CTX, required_approvers=2
    )
    assert db.rows[(CTX.tenant_id, request_id)]["required_approvers"] == 2

    # First vote lands on replica B — the gate must stay closed.
    assert await replica_b.approve_async(request_id, approver="alice", tenant_ctx=CTX)
    assert db.rows[(CTX.tenant_id, request_id)]["status"] == "pending"
    # The same approver voting again (on another replica) does not count twice.
    assert await replica_a.approve_async(request_id, approver="alice", tenant_ctx=CTX)
    assert db.rows[(CTX.tenant_id, request_id)]["status"] == "pending"
    req_a = replica_a.get_request(request_id, tenant_ctx=CTX)
    assert req_a is not None and req_a.status == ApprovalStatus.PENDING

    # A second distinct approver on replica A reaches the threshold.
    assert await replica_a.approve_async(request_id, approver="bob", tenant_ctx=CTX)
    assert db.resolutions == [(request_id, "approved")]
    assert req_a.status == ApprovalStatus.APPROVED
    assert req_a._event.is_set(), "the waiting agent on replica A is released"


@pytest.mark.asyncio
async def test_single_vote_does_not_release_a_two_person_gate_read_from_the_db() -> None:
    db, replica_a, replica_b = _pair()
    request_id = await replica_a.request_approval_async(
        goal_id="g1", action="drop table", tenant_ctx=CTX, required_approvers=2
    )
    # Replica B has never seen the request: it must learn the threshold from the DB.
    assert await replica_b.approve_async(request_id, approver="carol", tenant_ctx=CTX)
    assert db.resolutions == []


@pytest.mark.asyncio
async def test_wire_hitl_runtime_binds_redis_and_starts_rejection_subscriber() -> None:
    gw = HITLGateway()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    goal_service = MagicMock()

    wire_hitl_runtime(gw, redis=redis, goal_service=goal_service, redis_url="redis://x/0")

    assert gw._redis is redis
    goal_service.start_hitl_rejection_subscriber.assert_called_once_with("redis://x/0")


@pytest.mark.asyncio
async def test_approval_publishes_trigger_event_once_redis_is_wired() -> None:
    gw = HITLGateway()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    wire_hitl_runtime(gw, redis=redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe("hitl.approved")
    await pubsub.get_message(timeout=1)  # subscribe confirmation

    req = gw.request_approval(goal_id="g", action="deploy", tenant_ctx=CTX)
    assert await gw.approve_async(req.request_id, approver="op", tenant_ctx=CTX)
    await asyncio.sleep(0.05)  # publish is scheduled on the loop

    msg = await pubsub.get_message(timeout=1)
    assert msg is not None and msg["type"] == "message"
    assert await redis.llen(f"hitl_result:{req.request_id}") == 1  # cross-replica waiter


def test_main_lifespan_wires_hitl_runtime() -> None:
    import inspect

    import app.main as main_mod

    src = inspect.getsource(main_mod)
    assert "wire_hitl_runtime(" in src, "the lifespan must bind Redis into HITLGateway"


@pytest.mark.asyncio
async def test_slack_button_approval_goes_through_the_db_first_path() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import integrations

    gw = MagicMock()
    gw.approve_async = AsyncMock(return_value=True)
    gw.approve = MagicMock(side_effect=AssertionError("sync approve() must not be used"))
    app = FastAPI()
    app.include_router(integrations.router)
    app.state.hitl_gateway = gw
    body = {
        "type": "block_actions",
        "user": {"name": "sam"},
        "actions": [{"action_id": "approve_hitl", "value": "req-9"}],
    }
    with (
        patch.object(integrations, "_require_slack_signature", lambda *a, **k: None),
        patch.object(integrations, "_get_slack_tenant_id", lambda: "t-slack"),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/integrations/slack/events", json=body)
    assert resp.status_code == 200
    gw.approve_async.assert_awaited_once()
    assert gw.approve_async.await_args.kwargs["approver"] == "sam"


@pytest.mark.asyncio
async def test_org_task_approval_goes_through_the_db_first_path() -> None:
    import importlib
    import types

    org_router = importlib.import_module("app.org.router")

    gw = MagicMock()
    gw.approve_async = AsyncMock(return_value=True)
    gw.approve = MagicMock(side_effect=AssertionError("sync approve() must not be used"))
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(
        hitl_gateway=gw)))
    task = MagicMock()
    with patch.object(org_router, "_extract_hitl_request_id", lambda t: "req-org"):
        await org_router._resolve_task_hitl_request(
            request, "t-org", task, "approve", types.SimpleNamespace(approver="u", note="")
        )
    gw.approve_async.assert_awaited_once()
