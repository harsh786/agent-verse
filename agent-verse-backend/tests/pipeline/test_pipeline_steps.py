"""Tests for the 12-step tool-call pipeline (app/pipeline/steps.py)."""
from __future__ import annotations

import pytest

from app.governance.permissions import ActionLevel
from app.pipeline.steps import (
    circuit_breaker_check,
    cost_check,
    dedup_check,
    exec_memory_lookup,
    governance_check,
    hitl_gate,
    record_rollback_point,
    record_usage,
    result_processor_step,
)
from app.tenancy.context import PlanTier, TenantContext


def _tenant(tenant_id: str = "test-tenant") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test-key")


# ---------------------------------------------------------------------------
# cost_check
# ---------------------------------------------------------------------------


async def test_cost_check_no_controller_returns_true():
    """Graceful degradation: no controller means all steps are within budget."""
    result = await cost_check(step="do_something", tenant_ctx=_tenant(), controller=None)
    assert result is True


async def test_cost_check_within_budget_returns_true():
    from app.governance.cost import BudgetConfig, CostController

    cfg = BudgetConfig(per_goal_usd=1.0, per_tenant_daily_usd=100.0)
    controller = CostController(config=cfg)
    result = await cost_check(
        step="step1",
        tenant_ctx=_tenant(),
        controller=controller,
        goal_id="goal-1",
        estimated_cost=0.10,
    )
    assert result is True


async def test_cost_check_blocks_when_over_per_goal_budget():
    """A single expensive step that exceeds per_goal_usd must return False."""
    from app.governance.cost import BudgetConfig, CostController

    cfg = BudgetConfig(per_goal_usd=0.05, per_tenant_daily_usd=100.0)
    controller = CostController(config=cfg)
    # First call: within budget
    ok = await cost_check(
        step="step1",
        tenant_ctx=_tenant(),
        controller=controller,
        goal_id="g-budget",
        estimated_cost=0.04,
    )
    assert ok is True
    # Second call: would push goal total to 0.08 which exceeds 0.05
    blocked = await cost_check(
        step="step2",
        tenant_ctx=_tenant(),
        controller=controller,
        goal_id="g-budget",
        estimated_cost=0.04,
    )
    assert blocked is False


# ---------------------------------------------------------------------------
# governance_check
# ---------------------------------------------------------------------------


async def test_governance_check_no_matrix_returns_allow():
    """Graceful degradation: no matrix means all tools are allowed."""
    level = await governance_check(
        tool_name="read_file", tenant_ctx=_tenant(), matrix=None
    )
    assert level == ActionLevel.ALLOW


async def test_governance_check_deny_rule_blocks_tool():
    from app.governance.permissions import PermissionMatrix, PermissionRule

    ctx = _tenant()
    matrix = PermissionMatrix()
    matrix.set_rule(PermissionRule(tool_name="shell_exec", level=ActionLevel.DENY), tenant_ctx=ctx)

    level = await governance_check(tool_name="shell_exec", tenant_ctx=ctx, matrix=matrix)
    assert level == ActionLevel.DENY


async def test_governance_check_default_level_for_unknown_tool():
    from app.governance.permissions import PermissionMatrix

    level = await governance_check(
        tool_name="unknown_tool",
        tenant_ctx=_tenant(),
        matrix=PermissionMatrix(),
    )
    # Default for unconfigured tools is ALLOW_LOG
    assert level == ActionLevel.ALLOW_LOG


# ---------------------------------------------------------------------------
# dedup_check
# ---------------------------------------------------------------------------


async def test_dedup_check_no_cache_returns_false():
    """Graceful degradation: no cache means nothing is ever a duplicate."""
    result = await dedup_check(
        content_hash="abc123", tenant_ctx=_tenant(), cache=None
    )
    assert result is False


async def test_dedup_prevents_duplicate_calls():
    """mark_seen then is_duplicate must return True."""
    from app.reliability.dedup import DeduplicationCache

    ctx = _tenant()
    cache = DeduplicationCache()

    # First call — not a duplicate
    first = await dedup_check(content_hash="hash-xyz", tenant_ctx=ctx, cache=cache)
    assert first is False

    # Mark as seen
    cache.mark_seen(content_hash="hash-xyz", tenant_ctx=ctx)

    # Second call — is a duplicate
    second = await dedup_check(content_hash="hash-xyz", tenant_ctx=ctx, cache=cache)
    assert second is True


async def test_dedup_is_namespaced_per_tenant():
    """A hash seen for tenant A must not appear as duplicate for tenant B."""
    from app.reliability.dedup import DeduplicationCache

    cache = DeduplicationCache()
    ctx_a = _tenant("tenant-a")
    ctx_b = _tenant("tenant-b")

    cache.mark_seen(content_hash="shared-hash", tenant_ctx=ctx_a)

    assert await dedup_check(content_hash="shared-hash", tenant_ctx=ctx_a, cache=cache) is True
    assert await dedup_check(content_hash="shared-hash", tenant_ctx=ctx_b, cache=cache) is False


# ---------------------------------------------------------------------------
# hitl_gate
# ---------------------------------------------------------------------------


async def test_hitl_gate_no_gateway_returns_false():
    result = await hitl_gate(
        action="deploy_service", risk_level="high", tenant_ctx=_tenant(), gateway=None
    )
    assert result is False


async def test_hitl_gate_low_risk_auto_proceeds():
    from app.governance.hitl import HITLGateway

    gateway = HITLGateway()
    result = await hitl_gate(
        action="read_file", risk_level="low", tenant_ctx=_tenant(), gateway=gateway
    )
    assert result is False  # low risk → no approval request created


async def test_hitl_gate_high_risk_creates_request():
    from app.governance.hitl import HITLGateway

    gateway = HITLGateway()
    result = await hitl_gate(
        action="delete_database",
        risk_level="high",
        tenant_ctx=_tenant(),
        gateway=gateway,
        goal_id="g-hitl",
    )
    assert result is True  # approval request was logged


# ---------------------------------------------------------------------------
# result_processor_step
# ---------------------------------------------------------------------------


async def test_result_processor_passthrough_when_none():
    """Graceful degradation: no processor returns the raw output unchanged."""
    raw = "tool output: everything looks good"
    result = await result_processor_step(
        raw_output=raw, tenant_ctx=_tenant(), processor=None
    )
    assert result == raw


async def test_result_processor_redacts_openai_key():
    from app.reliability.result_processor import ResultProcessor

    processor = ResultProcessor()
    raw = "response contained sk-abc12345678 in the body"
    result = await result_processor_step(
        raw_output=raw, tenant_ctx=_tenant(), processor=processor
    )
    assert "sk-abc12345678" not in result


async def test_result_processor_truncates_long_output():
    from app.reliability.result_processor import ResultProcessor

    processor = ResultProcessor(max_length=50)
    raw = "x" * 200
    result = await result_processor_step(
        raw_output=raw, tenant_ctx=_tenant(), processor=processor
    )
    assert len(result) < 200
    assert "truncated" in result


# ---------------------------------------------------------------------------
# circuit_breaker_check
# ---------------------------------------------------------------------------


async def test_circuit_breaker_no_breaker_returns_false():
    """Graceful degradation: no breaker means circuit is always closed (open=False)."""
    result = await circuit_breaker_check(
        tool_name="some_tool", tenant_ctx=_tenant(), breaker=None
    )
    assert result is False


# ---------------------------------------------------------------------------
# record_rollback_point
# ---------------------------------------------------------------------------


async def test_record_rollback_point_no_engine_returns_empty():
    checkpoint = await record_rollback_point(
        action="create_file",
        inverse_action="delete_file",
        tenant_ctx=_tenant(),
        engine=None,
    )
    assert checkpoint == ""


async def test_record_rollback_point_returns_action_as_id():
    from app.reliability.rollback import RollbackEngine

    engine = RollbackEngine()
    checkpoint = await record_rollback_point(
        action="create_bucket",
        inverse_action="delete_bucket",
        tenant_ctx=_tenant(),
        engine=engine,
    )
    assert checkpoint == "create_bucket"
