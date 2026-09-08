"""D-1: DISTRIBUTED-tier GoalRuntimeProfiles must dispatch through StrategyRunner.

Before this change, GraphFactory always raised ValueError for a DISTRIBUTED profile (correct —
that tier has no LangGraph representation) and nothing caught it, so a DISTRIBUTED profile
either fell straight into GoalService's generic exception handler (goal_failed) or, if a
matching safety net existed, silently degraded to the local kernel with no attempt to actually
run the selected strategy. These tests verify GoalService now tries the real StrategyRunner
path first, and only falls back to the local kernel when the runner genuinely isn't usable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.orchestration.distributed_strategy_loop import DistributedStrategyLoop
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
from app.orchestration.strategy_context_store import StrategyGoalContextStore
from app.orchestration.strategy_contracts import ExecutionTerminalState
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner, _default_executor
from app.providers.fake import FakeProvider
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="dist-t1", plan=PlanTier.PROFESSIONAL, api_key_id="dk1")


def _distributed_profile(strategy_id: str = "supervisor") -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="goal-dist-1",
        tenant_id=T.tenant_id,
        properties=GoalProperties(raw_goal="coordinate a multi-part answer"),
        agent_patterns=AgentPatternConfig(reasoning=[strategy_id]),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection(strategy_id, "1.0.0"),
        execution_tier=ExecutionTier.DISTRIBUTED,
    )


def _base_app_state() -> MagicMock:
    app_state = MagicMock()
    app_state.audit_log = None
    app_state.cost_controller = None
    app_state.redis_cost_controller = None
    app_state.hitl_gateway = None
    app_state.knowledge_store = None
    app_state.long_term_memory = None
    app_state.eval_runner = None
    app_state.policy_engine = None
    app_state._llm_configs = {}
    return app_state


# ── is-it-wired ─────────────────────────────────────────────────────────────────────────────


def test_app_wires_a_real_strategy_executor_not_the_default() -> None:
    from app.main import create_app

    app = create_app(manage_pools=False)

    assert app.state.strategy_runner._executor is not _default_executor
    assert app.state.strategy_runner.has_real_executor is True
    assert app.state.strategy_goal_context_store is not None


# ── dispatch selection ──────────────────────────────────────────────────────────────────────


def test_distributed_profile_dispatches_through_runner_when_wired() -> None:
    app_state = _base_app_state()
    context_store = StrategyGoalContextStore()
    app_state.strategy_runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )
    app_state.strategy_goal_context_store = context_store

    svc = GoalService()
    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(
        T, app_state, agent_id="agent-1", runtime_profile=_distributed_profile()
    )

    assert isinstance(loop, DistributedStrategyLoop)
    assert loop.strategy_runner is app_state.strategy_runner
    assert loop.profile.primary_strategy.strategy_id == "supervisor"


def test_distributed_profile_falls_back_to_local_kernel_when_runner_not_wired() -> None:
    app_state = _base_app_state()
    app_state.strategy_runner = None  # runner never constructed / not available
    app_state.strategy_goal_context_store = None

    svc = GoalService()
    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(
        T, app_state, agent_id="agent-1", runtime_profile=_distributed_profile()
    )

    assert isinstance(loop, AgentGraph)


def test_distributed_profile_falls_back_when_runner_still_has_default_executor() -> None:
    app_state = _base_app_state()
    app_state.strategy_runner = StrategyRunner(build_default_registry())  # inert default
    app_state.strategy_goal_context_store = StrategyGoalContextStore()

    svc = GoalService()
    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(
        T, app_state, agent_id="agent-1", runtime_profile=_distributed_profile()
    )

    assert isinstance(loop, AgentGraph)


def test_non_distributed_profile_still_uses_graph_factory() -> None:
    """Sanity check: this change must not touch the existing LOCAL-tier compile path."""
    app_state = _base_app_state()
    app_state.strategy_runner = None
    svc = GoalService()
    svc._app_state = app_state

    local_profile = _distributed_profile("react")
    object.__setattr__(local_profile, "execution_tier", ExecutionTier.LOCAL)

    loop = svc._make_agent_loop_for_tenant(
        T, app_state, agent_id="agent-1", runtime_profile=local_profile
    )

    assert isinstance(loop, AgentGraph)


# ── end-to-end loop.run() folds the result back into the goal lifecycle ────────────────────


@pytest.mark.asyncio
async def test_distributed_loop_run_emits_goal_complete_on_success() -> None:
    context_store = StrategyGoalContextStore()
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )
    provider = FakeProvider(
        responses=['{"steps": [{"id": "s1", "summary": "do it"}]}', "sub-answer", "final answer"]
    )
    loop = DistributedStrategyLoop(
        strategy_runner=runner,
        context_store=context_store,
        profile=_distributed_profile(),
        provider=provider,
        agent_id="agent-1",
    )

    events: list[dict[str, object]] = []

    async def callback(event: dict[str, object]) -> None:
        events.append(event)

    result = await loop.run(
        goal="coordinate a multi-part answer",
        tenant_ctx=T,
        event_callback=callback,
        goal_id="goal-dist-1",
    )

    assert result["terminal_state"] == ExecutionTerminalState.SUCCEEDED.value
    assert events[-1]["type"] == "goal_complete"
    assert events[-1]["answer"]
    # the per-goal context must not leak past the call
    assert context_store.get(f"strategy-goal-context://{T.tenant_id}:goal-dist-1") is None


@pytest.mark.asyncio
async def test_distributed_loop_run_emits_goal_failed_when_strategy_is_denied() -> None:
    context_store = StrategyGoalContextStore()
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )
    loop = DistributedStrategyLoop(
        strategy_runner=runner,
        context_store=context_store,
        profile=_distributed_profile("autogpt"),
        provider=FakeProvider(),
        agent_id="agent-1",
    )

    events: list[dict[str, object]] = []

    async def callback(event: dict[str, object]) -> None:
        events.append(event)

    result = await loop.run(
        goal="do something autonomous",
        tenant_ctx=T,
        event_callback=callback,
        goal_id="goal-dist-2",
    )

    assert result["terminal_state"] == ExecutionTerminalState.POLICY_DENIED.value
    assert events[-1]["type"] == "goal_failed"
    reason = str(events[-1]["reason"])
    assert "strategy_execution_not_implemented" in reason
