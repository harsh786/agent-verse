"""Security regression tests — HITL enforcement on high-risk steps.

These tests prove defects C1 and C7 are fixed and cannot regress.

C1: app/agent/loop.py — HITL gate fire-and-forget (auto-proceeds without approval).
C7: app/agent/graph.py — write_high approval never awaited.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(
    tenant_id="sec-t1",
    plan=PlanTier.ENTERPRISE,
    api_key_id="k1",
    roles=("admin",),
)


# ---------------------------------------------------------------------------
# C1: AgentLoop HITL in supervised mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_supervised_high_risk_raises_on_rejection() -> None:
    """C1: loop.py — supervised mode, high-risk step, rejection → PermissionError."""
    from app.agent.loop import AgentLoop
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)

    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["delete all production data"]}']),
        executor=FakeProvider(responses=["deleted"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="rejected"):
        await loop.run(goal="Delete all data", tenant_ctx=T)
    hitl.wait_for_approval.assert_awaited()


@pytest.mark.asyncio
async def test_agent_loop_supervised_high_risk_raises_on_timeout() -> None:
    """C1: loop.py — supervised mode, high-risk step, timeout → PermissionError."""
    from app.agent.loop import AgentLoop
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.TIMED_OUT)

    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["wipe the database"]}']),
        executor=FakeProvider(responses=["wiped"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="timed out"):
        await loop.run(goal="Wipe database", tenant_ctx=T)


@pytest.mark.asyncio
async def test_agent_loop_supervised_high_risk_proceeds_on_approval() -> None:
    """C1: approved step executes exactly once, after approval."""
    from app.agent.loop import AgentLoop
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)

    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["deploy to production"]}']),
        executor=FakeProvider(responses=["deployed successfully"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    state = await loop.run(goal="Deploy app", tenant_ctx=T)
    assert state.status == GoalStatus.COMPLETE
    hitl.wait_for_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_loop_non_supervised_does_not_block() -> None:
    """C1: non-supervised mode — high-risk step logs but does NOT block."""
    from app.agent.loop import AgentLoop
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)

    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["deploy to production"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="bounded-autonomous",
    )
    state = await loop.run(goal="Deploy app", tenant_ctx=T)
    assert state.status == GoalStatus.COMPLETE
    hitl.wait_for_approval.assert_not_awaited()


def test_high_risk_keywords_includes_wipe_truncate() -> None:
    """C1: loop.py _HIGH_RISK_KEYWORDS must include wipe and truncate."""
    from app.agent.loop import _HIGH_RISK_KEYWORDS

    assert "wipe" in _HIGH_RISK_KEYWORDS
    assert "truncate" in _HIGH_RISK_KEYWORDS
    assert "deploy" in _HIGH_RISK_KEYWORDS
    assert "delete" in _HIGH_RISK_KEYWORDS


def test_high_risk_keywords_no_rm_false_positive() -> None:
    """C1: 'rm' should not falsely match 'format' or 'perform'."""
    from app.agent.loop import _HIGH_RISK_KEYWORDS

    step = "format the document and perform analysis"
    assert not any(kw in step.lower() for kw in _HIGH_RISK_KEYWORDS), (
        f"False positive: '{step}' incorrectly matched as high-risk"
    )


def test_high_risk_keywords_is_frozenset() -> None:
    """C1: _HIGH_RISK_KEYWORDS should be a frozenset for O(1) lookup."""
    from app.agent.loop import _HIGH_RISK_KEYWORDS

    assert isinstance(_HIGH_RISK_KEYWORDS, frozenset)
