"""D-1 (safety slice): a DISTRIBUTED-tier runtime profile must not hard-fail a
goal. The full StrategyRunner distributed executor is a separate workstream, but
until it is wired, ``GraphFactory.create`` raises
``ValueError("distributed strategy requires StrategyRunner")`` — which surfaced
to the user as an opaque ``goal_failed``. This locks graceful, observable
degradation to the local AgentGraph kernel so the goal still executes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
    StrategySelection,
)
from app.orchestration.strategy_adapters import ExecutionTier
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext


class _FakeAgentStore:
    def get(self, agent_id: str, *, tenant_ctx: Any = None) -> dict[str, Any]:
        return {}


def _svc() -> GoalService:
    svc = GoalService.__new__(GoalService)
    svc._audit_log = None
    svc._db = None
    svc._hitl = None
    svc._get_agent_store = lambda: _FakeAgentStore()  # type: ignore[method-assign]
    svc._get_mcp_client = lambda: None  # type: ignore[method-assign]
    svc._select_models_for_tenant = lambda tenant_ctx: {}  # type: ignore[method-assign]
    return svc


def _profile(*strategies: str) -> GoalRuntimeProfile:
    primary, *auxiliary = strategies or ("rewoo",)
    return GoalRuntimeProfile(
        goal_id="goal-d1",
        tenant_id="t-d1",
        properties=GoalProperties(raw_goal="goal"),
        agent_patterns=AgentPatternConfig(reasoning=list(strategies or ("rewoo",))),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection(primary, "1.0.0"),
        auxiliary_strategies=tuple(StrategySelection(i, "1.0.0") for i in auxiliary),
    )


_CTX = TenantContext(tenant_id="t-d1", plan=PlanTier.FREE, api_key_id="k1")


def test_distributed_tier_profile_degrades_to_local_graph_not_hard_fail() -> None:
    distributed = replace(_profile("rewoo"), execution_tier=ExecutionTier.DISTRIBUTED)
    graph = _svc()._make_agent_loop_for_tenant(_CTX, None, runtime_profile=distributed)
    # A usable local kernel is returned (goal can still execute) instead of raising.
    assert graph is not None
    node_names = set(graph._graph.get_graph().nodes.keys())
    assert {"plan", "execute", "verify"} <= node_names


def test_local_tier_profile_still_uses_graph_factory() -> None:
    """Regression guard: a LOCAL-tier profile keeps compiling through GraphFactory
    (it carries the runtime_profile so profile-derived flags still apply)."""
    local = _profile("react")  # default tier is LOCAL
    graph = _svc()._make_agent_loop_for_tenant(_CTX, None, runtime_profile=local)
    assert graph is not None
    assert graph.runtime_profile is not None
    assert graph.runtime_profile.goal_id == "goal-d1"


def test_distributed_supervisor_falls_back_to_the_local_supervisor_node() -> None:
    """Without a StrategyRunner the local kernel still runs the requested supervisor
    node (it has one) and the goal records the tier downgrade — not a bare ReAct loop
    under the supervisor's name."""
    context: dict[str, Any] = {}
    distributed = replace(_profile("supervisor"), execution_tier=ExecutionTier.DISTRIBUTED)
    graph = _svc()._make_agent_loop_for_tenant(
        _CTX, None, runtime_profile=distributed, execution_context=context
    )
    assert graph._enable_supervisor is True
    execution = context["strategy_execution"]
    assert execution["driver"] == "agent_graph"
    assert "supervisor" in execution["patterns"]
    assert execution["downgrades"] == [
        {
            "strategy_id": "supervisor",
            "from": "strategy_runner",
            "to": "agent_graph",
            "reason": "strategy_runner_unavailable",
        }
    ]


def test_distributed_primary_without_local_node_records_react_downgrade() -> None:
    context: dict[str, Any] = {}
    distributed = replace(_profile("rewoo"), execution_tier=ExecutionTier.DISTRIBUTED)
    _svc()._make_agent_loop_for_tenant(
        _CTX, None, runtime_profile=distributed, execution_context=context
    )
    execution = context["strategy_execution"]
    assert execution["patterns"] == ["react"]
    assert execution["downgrades"][0]["reason"] == "strategy_runner_unavailable_no_local_node"


def test_legacy_graph_records_the_patterns_it_runs() -> None:
    context: dict[str, Any] = {}
    _svc()._make_agent_loop_for_tenant(_CTX, None, execution_context=context)
    assert context["strategy_execution"] == {"driver": "agent_graph", "patterns": ["react"]}


def test_profile_graph_records_profile_patterns() -> None:
    context: dict[str, Any] = {}
    _svc()._make_agent_loop_for_tenant(
        _CTX,
        None,
        runtime_profile=_profile("react", "reflection"),
        execution_context=context,
    )
    execution = context["strategy_execution"]
    assert execution["patterns"] == ["react", "reflection"]
    assert execution["requested_primary"] == "react"
    assert "downgrades" not in execution
