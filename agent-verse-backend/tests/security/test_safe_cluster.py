"""SAFE cluster (P0-12..P0-15) — live safety layers must actually enforce.

These regression tests prove that safety layers which previously no-op'd now
enforce and fail CLOSED:

  P0-12 SAFE-1 — PermissionMatrix is wired and default-denies destructive tools.
  P0-13 SAFE-2 — discovered tools are registered so hallucinated names are rejected.
  P0-14 SAFE-3 — an action-safety HITL_REQUIRED verdict routes through the HITL gateway.
  P0-15 SAFE-4 — an errored guardrail check fails closed on high-risk work.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import (
    ActionLevel,
    PermissionRule,
    build_default_permission_matrix,
)
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(
    tenant_id="safe-t1",
    plan=PlanTier.ENTERPRISE,
    api_key_id="k1",
    roles=("admin",),
)


def _graph(**kwargs):
    from app.agent.graph import AgentGraph

    defaults: dict = {
        "planner": FakeProvider(responses=['{"steps": ["do the thing"]}']),
        "executor": FakeProvider(responses=["done"]),
        "verifier": FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    }
    defaults.update(kwargs)
    return AgentGraph(**defaults)


# ───────────────────────────── P0-12 SAFE-1 ──────────────────────────────────


def test_permission_matrix_default_denies_destructive_and_allows_benign() -> None:
    matrix = build_default_permission_matrix()
    assert matrix.check("delete_all_records", tenant_ctx=T) == ActionLevel.DENY
    assert matrix.check("drop_database", tenant_ctx=T) == ActionLevel.DENY
    assert matrix.check("wipe_disk", tenant_ctx=T) == ActionLevel.DENY
    # Benign tools keep the audit-only default.
    assert matrix.check("list_files", tenant_ctx=T) == ActionLevel.ALLOW_LOG
    assert matrix.check("read_document", tenant_ctx=T) == ActionLevel.ALLOW_LOG


def test_permission_matrix_tenant_allow_overrides_default_deny() -> None:
    matrix = build_default_permission_matrix()
    assert matrix.check("delete_temp", tenant_ctx=T) == ActionLevel.DENY
    matrix.set_rule(
        PermissionRule(tool_name="delete_temp", level=ActionLevel.ALLOW), tenant_ctx=T
    )
    assert matrix.check("delete_temp", tenant_ctx=T) == ActionLevel.ALLOW


@pytest.mark.asyncio
async def test_destructive_tool_passes_without_matrix_today() -> None:
    """Proves the gap: with no matrix (the current wiring) a destructive tool runs."""
    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call delete_all_records now"]}']),
        permission_matrix=None,
    )
    state = await graph.run(goal="Delete all records", tenant_ctx=T)
    # No PermissionError raised — the step executed.
    assert state is not None


@pytest.mark.asyncio
async def test_destructive_tool_denied_when_matrix_wired() -> None:
    """After wiring the default-deny matrix, the same step is blocked."""
    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call delete_all_records now"]}']),
        permission_matrix=build_default_permission_matrix(),
    )
    with pytest.raises(PermissionError, match="denied by governance"):
        await graph.run(goal="Delete all records", tenant_ctx=T)


def test_create_app_binds_permission_matrix() -> None:
    from app.governance.permissions import PermissionMatrix
    from app.main import create_app

    app = create_app()
    assert isinstance(app.state.permission_matrix, PermissionMatrix)
    assert app.state.permission_matrix.check("destroy_cluster", tenant_ctx=T) == ActionLevel.DENY


def test_goal_service_threads_permission_matrix_into_graph() -> None:
    from app.services.goal_service import GoalService

    matrix = build_default_permission_matrix()
    app_state = MagicMock()
    app_state.permission_matrix = matrix
    app_state.audit_log = None
    app_state.cost_controller = None
    app_state.redis_cost_controller = None
    app_state.hitl_gateway = None
    app_state.knowledge_store = None
    app_state.long_term_memory = None
    app_state.eval_runner = None
    app_state.policy_engine = None
    app_state._llm_configs = {}

    svc = GoalService()
    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(T, app_state)
    assert loop._permission_matrix is matrix


# ───────────────────────────── P0-13 SAFE-2 ──────────────────────────────────


def test_register_tools_from_context_populates_checker() -> None:
    from app.agent.tool_context import ToolContext, ToolRef
    from app.intelligence.guardrails import GuardrailChecker
    from app.services.goal_service import GoalService

    checker = GuardrailChecker()
    assert checker._known_tools == set()

    tools = [
        ToolRef(
            server_id="s1",
            server_name="s1",
            name="real_search",
            description="",
            input_schema={},
        ),
        ToolRef(
            server_id="s1",
            server_name="s1",
            name="real_fetch",
            description="",
            input_schema={},
        ),
    ]
    ctx = ToolContext(connectors=[], tools=tools)
    loop = SimpleNamespace(_guardrail_checker=checker)

    GoalService._register_tools_from_context(loop, ctx)
    assert "real_search" in checker._known_tools
    assert "real_fetch" in checker._known_tools


@pytest.mark.asyncio
async def test_hallucinated_tool_accepted_with_empty_registry_today() -> None:
    """Proves the gap: an empty known-tools registry accepts any tool name."""
    from app.intelligence.guardrails import GuardrailChecker

    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call totally_made_up_tool_9000"]}']),
        guardrail_checker=GuardrailChecker(),  # empty registry, as goal_service did
    )
    state = await graph.run(goal="Use a tool", tenant_ctx=T)
    # Not blocked — step ran.
    assert not any(
        "not in known-tools registry" in (s.output or "") for s in state.steps
    )


@pytest.mark.asyncio
async def test_hallucinated_tool_rejected_after_registration() -> None:
    from app.intelligence.guardrails import GuardrailChecker

    checker = GuardrailChecker()
    checker.register_tools({"real_search", "real_fetch"})
    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call totally_made_up_tool_9000"]}']),
        guardrail_checker=checker,
    )
    state = await graph.run(goal="Use a tool", tenant_ctx=T)
    assert any("not in known-tools registry" in (s.output or "") for s in state.steps)


@pytest.mark.asyncio
async def test_real_registered_tool_still_passes() -> None:
    from app.intelligence.guardrails import GuardrailChecker

    checker = GuardrailChecker()
    checker.register_tools({"real_search"})
    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call real_search for data"]}']),
        guardrail_checker=checker,
    )
    state = await graph.run(goal="Search", tenant_ctx=T)
    assert not any(
        "not in known-tools registry" in (s.output or "") for s in state.steps
    )


# ───────────────────────────── P0-14 SAFE-3 ──────────────────────────────────


@pytest.mark.asyncio
async def test_action_safety_hitl_required_blocks_on_rejection() -> None:
    """A high-risk (non-keyword) action must gate through HITL, not silently run."""
    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-asp")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)

    graph = _graph(
        planner=FakeProvider(
            responses=['{"steps": ["notify the finance team about quarterly payroll"]}']
        ),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="action-safety"):
        await graph.run(
            goal="Process payroll",
            tenant_ctx=T,
            initial_context={"_risk_level": "high"},
        )
    hitl.wait_for_approval.assert_awaited()


@pytest.mark.asyncio
async def test_action_safety_hitl_required_proceeds_on_approval() -> None:
    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="req-asp")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)

    graph = _graph(
        planner=FakeProvider(
            responses=['{"steps": ["notify the finance team about quarterly payroll"]}']
        ),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    state = await graph.run(
        goal="Process payroll",
        tenant_ctx=T,
        initial_context={"_risk_level": "high"},
    )
    hitl.wait_for_approval.assert_awaited()
    assert state is not None


# ───────────────────────────── P0-15 SAFE-4 ──────────────────────────────────


def test_guardrail_should_fail_closed_helper() -> None:
    from app.agent.nodes._helpers import _guardrail_should_fail_closed

    # High-risk keyword step → fail closed even without an explicit risk level.
    assert _guardrail_should_fail_closed("delete the production database", None) is True
    # Explicit high / critical risk level → fail closed regardless of step text.
    assert _guardrail_should_fail_closed("summarise the report", "high") is True
    assert _guardrail_should_fail_closed("summarise the report", "critical") is True
    # Benign, low-risk → fail open (do not block on transient guardrail errors).
    assert _guardrail_should_fail_closed("summarise the report", "low") is False
    assert _guardrail_should_fail_closed("summarise the report", None) is False


@pytest.mark.asyncio
async def test_guardrail_engine_error_fails_open_on_low_risk() -> None:
    """A guardrail engine error on a benign step does NOT block (current behavior)."""
    engine = MagicMock()
    engine.evaluate_tool_args = AsyncMock(side_effect=RuntimeError("boom"))
    app_state = SimpleNamespace(guardrail_engine=engine)

    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call fetch_weather for London"]}']),
        executor=FakeProvider(
            responses=['{"tool": "fetch_weather", "arguments": {"city": "London"}}']
        ),
    )
    graph._app_state = app_state
    # Should not raise — low risk, fail open.
    state = await graph.run(goal="Get weather", tenant_ctx=T)
    assert state is not None


@pytest.mark.asyncio
async def test_guardrail_engine_error_fails_closed_on_high_risk() -> None:
    """A guardrail engine error on a high-risk tool call must BLOCK (fail closed)."""
    engine = MagicMock()
    engine.evaluate_tool_args = AsyncMock(side_effect=RuntimeError("boom"))
    app_state = SimpleNamespace(guardrail_engine=engine)

    graph = _graph(
        planner=FakeProvider(responses=['{"steps": ["call delete_customer for acme"]}']),
        executor=FakeProvider(
            responses=['{"tool": "delete_customer", "arguments": {"id": "acme"}}']
        ),
    )
    graph._app_state = app_state
    with pytest.raises(PermissionError, match="failing closed"):
        await graph.run(goal="Remove customer", tenant_ctx=T)
