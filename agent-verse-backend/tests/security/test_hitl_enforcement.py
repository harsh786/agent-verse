"""Security regression tests — HITL enforcement on high-risk steps.

These tests prove defects C1 and C7 are fixed and cannot regress.

C1/C7: the canonical AgentGraph must await HITL decisions for high-risk work.
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
# Canonical AgentGraph HITL in supervised mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_graph_supervised_high_risk_raises_on_rejection() -> None:
    """Supervised high-risk work fails when approval is rejected."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["delete all production data"]}']),
        executor=FakeProvider(responses=["deleted"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="rejected"):
        await graph.run(goal="Delete all data", tenant_ctx=T)
    hitl.wait_for_approval.assert_awaited()


@pytest.mark.asyncio
async def test_agent_graph_supervised_high_risk_raises_on_timeout() -> None:
    """Supervised high-risk work fails when approval times out."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.TIMED_OUT)

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["wipe the database"]}']),
        executor=FakeProvider(responses=["wiped"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="timed out"):
        await graph.run(goal="Wipe database", tenant_ctx=T)


@pytest.mark.asyncio
async def test_agent_graph_supervised_high_risk_proceeds_on_approval() -> None:
    """C1: approved step executes exactly once, after approval."""
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["deploy to production"]}']),
        executor=FakeProvider(responses=["deployed successfully"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    state = await graph.run(goal="Deploy app", tenant_ctx=T)
    assert state.status == GoalStatus.COMPLETE
    hitl.wait_for_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_graph_non_supervised_does_not_block() -> None:
    """C1: non-supervised mode — high-risk step logs but does NOT block."""
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider

    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["deploy to production"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
        autonomy_mode="bounded-autonomous",
    )
    state = await graph.run(goal="Deploy app", tenant_ctx=T)
    assert state.status == GoalStatus.COMPLETE
    hitl.wait_for_approval.assert_not_awaited()


def test_high_risk_keywords_includes_wipe_truncate() -> None:
    """The canonical graph risk vocabulary includes destructive operations."""
    from app.agent.graph import _HIGH_RISK_KEYWORDS

    assert "wipe" in _HIGH_RISK_KEYWORDS
    assert "truncate" in _HIGH_RISK_KEYWORDS
    assert "deploy" in _HIGH_RISK_KEYWORDS
    assert "delete" in _HIGH_RISK_KEYWORDS


def test_high_risk_keywords_no_rm_false_positive() -> None:
    """C1: 'rm' should not falsely match 'format' or 'perform'."""
    from app.agent.nodes._helpers import _is_high_risk_step

    step = "format the document and perform analysis"
    assert not _is_high_risk_step(step)
    assert _is_high_risk_step("rm -rf temporary-output")


def test_high_risk_keywords_is_frozenset() -> None:
    """C1: _HIGH_RISK_KEYWORDS should be a frozenset for O(1) lookup."""
    from app.agent.graph import _HIGH_RISK_KEYWORDS

    assert isinstance(_HIGH_RISK_KEYWORDS, frozenset)
