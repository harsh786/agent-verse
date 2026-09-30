"""CORE-02: an approval gate is durable before anyone waits on it.

``request_approval`` persisted the row in an unreferenced background task
whose failure was only logged, so a supervised goal could wait the whole
timeout on a request no other replica (and so no approver) could see. And
``wait_for_approval`` returned REJECTED for any id missing from this
process's dict, even when the request existed in Postgres.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.governance.hitl import ApprovalStatus, HITLDeliveryError, HITLGateway
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-durable", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _failing_persist_gateway() -> HITLGateway:
    gw = HITLGateway()
    gw._db_session_factory = object()  # a DB is configured; the write is stubbed

    async def _boom(req: Any, tenant_id: str, *, raise_errors: bool = False) -> None:
        if raise_errors:
            raise RuntimeError("db down")

    gw._db_persist_approval_request = _boom  # type: ignore[method-assign]
    return gw


async def test_request_approval_async_does_not_double_persist() -> None:
    gw = HITLGateway()
    gw._db_session_factory = object()
    calls: list[str] = []

    async def _record(req: Any, tenant_id: str, *, raise_errors: bool = False) -> None:
        calls.append(req.request_id)

    gw._db_persist_approval_request = _record  # type: ignore[method-assign]
    req_id = await gw.request_approval_async(
        goal_id="g", action="deploy", tenant_ctx=CTX, require_persisted=True
    )
    await asyncio.sleep(0)  # let any stray background task run
    assert calls == [req_id]


async def test_request_approval_async_raises_and_forgets_on_persist_failure() -> None:
    gw = _failing_persist_gateway()
    with pytest.raises(HITLDeliveryError):
        await gw.request_approval_async(
            goal_id="g", action="deploy", tenant_ctx=CTX, require_persisted=True
        )
    assert gw.list_pending(tenant_ctx=CTX) == []


async def test_wait_for_unknown_local_id_uses_the_db_request() -> None:
    """A request raised on another replica is waited on, not read as REJECTED."""
    gw = HITLGateway()
    gw._db_session_factory = object()
    gw._db_fetch_request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "id": "req-remote",
            "tenant_id": CTX.tenant_id,
            "goal_id": "g",
            "action": "deploy",
            "risk_level": "high",
            "status": "pending",
            "required_approvers": 1,
        }
    )
    # No Redis channel: the decision made elsewhere arrives through Postgres.
    gw._db_read_status = AsyncMock(side_effect=["pending", "approved"])  # type: ignore[method-assign]
    gw._DB_POLL_INTERVAL_S = 0.01  # type: ignore[misc]

    status = await gw.wait_for_approval("req-remote", tenant_ctx=CTX, timeout=2.0)

    assert status == ApprovalStatus.APPROVED


async def test_wait_for_id_unknown_everywhere_is_rejected() -> None:
    gw = HITLGateway()
    gw._db_session_factory = object()
    gw._db_fetch_request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    status = await gw.wait_for_approval("nope", tenant_ctx=CTX, timeout=0.2)
    assert status == ApprovalStatus.REJECTED


async def test_wait_returns_an_already_decided_db_request_immediately() -> None:
    gw = HITLGateway()
    gw._db_session_factory = object()
    gw._db_fetch_request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "id": "req-done",
            "tenant_id": CTX.tenant_id,
            "goal_id": "g",
            "action": "deploy",
            "risk_level": "high",
            "status": "rejected",
            "required_approvers": 1,
        }
    )
    status = await asyncio.wait_for(
        gw.wait_for_approval("req-done", tenant_ctx=CTX, timeout=30.0), timeout=2.0
    )
    assert status == ApprovalStatus.REJECTED


async def test_executor_gate_fails_fast_when_the_request_cannot_be_persisted() -> None:
    """The supervised gate must not wait the full timeout on an invisible request."""
    gw = _failing_persist_gateway()
    gw.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)  # type: ignore[method-assign]
    executor = FakeProvider(responses=["deployed"])
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=gw,
        autonomy_mode="supervised",
    )
    step = "deploy the service to production"
    state = AgentState(goal="goal", tenant_ctx=CTX)
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))

    with pytest.raises(PermissionError, match="could not be persisted"):
        await graph._execute_step(step, state, CTX)

    gw.wait_for_approval.assert_not_awaited()
    assert executor.call_history == []
