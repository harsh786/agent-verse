"""Every pattern a goal's profile names must be one that actually runs.

Regression: GraphFactory mapped only eight strategy flags, so a profile naming ReWOO,
LATS, CodeAct, … compiled a plain AgentGraph while claiming the requested pattern, and
the selector listed ``consensus`` as a multi-agent topology nothing executes.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from app.orchestration.execution_drivers import (
    AGENT_GRAPH_STRATEGY_FLAGS,
    SINGLE_AGENT,
    STRATEGY_RUNNER_STRATEGIES,
    ExecutionDriver,
    describe_agent_graph_execution,
    goal_execution_driver,
    is_goal_executable,
)
from app.orchestration.graph_factory import GraphFactory
from app.orchestration.pattern_selection_summary import summarize_pattern_selection
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    Complexity,
    Domain,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
    StrategySelection,
    TimeSensitivity,
)
from app.orchestration.runtime_profile_builder import (
    InvalidStrategyOverrideError,
    RuntimeProfileBuilder,
)
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_executor import SUPPORTED_DISTRIBUTED_STRATEGIES
from app.orchestration.strategy_registry import build_default_registry
from app.providers.fake import FakeProvider


def _all_props() -> list[GoalProperties]:
    grid = itertools.product(
        Complexity,
        Domain,
        RiskLevel,
        TimeSensitivity,
        (False, True),  # requires_code
        (False, True),  # is_generative
        (False, True),  # multi_step
    )
    return [
        GoalProperties(
            raw_goal="goal",
            complexity=complexity,
            domain=domain,
            risk=risk,
            time_sensitivity=time_sensitivity,
            requires_code=requires_code,
            is_generative=is_generative,
            multi_step=multi_step,
        )
        for (
            complexity,
            domain,
            risk,
            time_sensitivity,
            requires_code,
            is_generative,
            multi_step,
        ) in grid
    ]


def test_every_id_the_selector_can_emit_is_registered_and_compilable() -> None:
    registry = build_default_registry()
    selector = PatternSelector(registry)
    reasoning: set[str] = set()
    multi_agent: set[str] = set()
    safety: set[str] = set()
    for props in _all_props():
        cfg = selector.select_agent_patterns(props)
        reasoning.update(cfg.reasoning)
        multi_agent.update(cfg.multi_agent)
        safety.update(cfg.safety)
    multi_agent.discard(SINGLE_AGENT)

    for strategy_id in reasoning | multi_agent | safety:
        assert registry.get(strategy_id) is not None, strategy_id
    # Reasoning and multi-agent picks are consumed by the AgentGraph kernel — each one
    # must map to a node/flag GraphFactory can compile in.
    for strategy_id in reasoning | multi_agent:
        assert strategy_id in AGENT_GRAPH_STRATEGY_FLAGS, strategy_id
        assert is_goal_executable(registry, strategy_id), strategy_id


def test_driver_table_names_only_registered_strategies() -> None:
    registry = build_default_registry()
    for strategy_id in AGENT_GRAPH_STRATEGY_FLAGS:
        assert registry.get(strategy_id) is not None, strategy_id
    assert STRATEGY_RUNNER_STRATEGIES == SUPPORTED_DISTRIBUTED_STRATEGIES


@pytest.mark.parametrize(
    ("strategy_id", "driver"),
    [
        ("react", ExecutionDriver.AGENT_GRAPH),
        ("tree_of_thoughts", ExecutionDriver.AGENT_GRAPH),
        ("supervisor", ExecutionDriver.STRATEGY_RUNNER),
        ("rewoo", None),
        ("lats", None),
        ("codeact", None),
        ("program_of_thought", None),
        ("magentic", None),
        ("consensus", None),
        ("scratchpad", None),
        ("workflow_dag", None),
    ],
)
def test_goal_execution_driver(strategy_id: str, driver: ExecutionDriver | None) -> None:
    capability = build_default_registry().resolve(strategy_id).capability
    assert goal_execution_driver(capability) == driver


def _profile(*strategies: str, tier: ExecutionTier = ExecutionTier.LOCAL) -> GoalRuntimeProfile:
    primary, *auxiliary = strategies
    return GoalRuntimeProfile(
        goal_id="goal-1",
        tenant_id="tenant-1",
        properties=GoalProperties(raw_goal="goal"),
        agent_patterns=AgentPatternConfig(reasoning=list(strategies)),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection(primary, "1.0.0"),
        auxiliary_strategies=tuple(StrategySelection(item, "1.0.0") for item in auxiliary),
        execution_tier=tier,
    )


def _services() -> dict[str, object]:
    provider = FakeProvider()
    return {"planner": provider, "executor": provider, "verifier": provider}


@pytest.mark.parametrize("strategy_id", ["rewoo", "lats", "llm_compiler", "scratchpad"])
def test_graph_factory_refuses_a_primary_the_kernel_cannot_run(strategy_id: str) -> None:
    with pytest.raises(ValueError, match="no AgentGraph driver"):
        GraphFactory().create(_profile(strategy_id), _services())


def test_graph_factory_refuses_an_auxiliary_the_kernel_cannot_run() -> None:
    with pytest.raises(ValueError, match="no AgentGraph driver"):
        GraphFactory().create(_profile("react", "graph_of_thoughts"), _services())


@pytest.mark.parametrize("tier", [ExecutionTier.SANDBOX, ExecutionTier.WORKFLOW, ExecutionTier.RAG])
def test_graph_factory_refuses_non_local_tiers(tier: ExecutionTier) -> None:
    with pytest.raises(ValueError, match="no AgentGraph driver"):
        GraphFactory().create(_profile("react", tier=tier), _services())


def test_graph_factory_compiles_goal_tree_and_reports_what_runs() -> None:
    graph = GraphFactory().create(_profile("react", "goal_tree", "reflection"), _services())
    assert graph._enable_goal_tree is True
    assert describe_agent_graph_execution(graph) == ["react", "reflection", "goal_tree"]


def test_describe_reports_plan_execute_as_the_base_loop() -> None:
    graph = GraphFactory().create(_profile("plan_execute"), _services())
    assert describe_agent_graph_execution(graph) == ["plan_execute"]


async def test_explicit_primary_without_driver_is_refused() -> None:
    with pytest.raises(InvalidStrategyOverrideError, match="no goal execution driver"):
        await RuntimeProfileBuilder().build(
            "goal", tenant_id="t1", goal_id="g1", agent_config={"primary_strategy": "rewoo"}
        )


async def test_explicit_distributed_primary_needs_coordination() -> None:
    with pytest.raises(InvalidStrategyOverrideError, match="coordination_not_ready"):
        await RuntimeProfileBuilder().build(
            "goal",
            tenant_id="t1",
            goal_id="g1",
            agent_config={"primary_strategy": "supervisor"},
        )
    profile = await RuntimeProfileBuilder().build(
        "goal",
        tenant_id="t1",
        goal_id="g1",
        agent_config={"primary_strategy": "supervisor", "coordination_ready": True},
    )
    assert profile.primary_strategy.strategy_id == "supervisor"
    assert profile.execution_tier is ExecutionTier.DISTRIBUTED


async def test_undriven_auxiliary_is_recorded_as_rejected() -> None:
    profile = await RuntimeProfileBuilder().build(
        "goal",
        tenant_id="t1",
        goal_id="g1",
        agent_config={"auxiliary_strategies": ["rewoo", "reflection"]},
    )
    assert [item.strategy_id for item in profile.auxiliary_strategies] == ["reflection"]
    assert ("rewoo", "no_execution_driver") in {
        (item.strategy_id, item.reason_code) for item in profile.rejected_alternatives
    }


async def test_critical_goal_records_consensus_downgrade_in_profile() -> None:
    profile = await RuntimeProfileBuilder().build(
        "delete the production billing database and purge all backups",
        tenant_id="t1",
        goal_id="g1",
    )
    assert profile.properties.risk is RiskLevel.CRITICAL
    assert "consensus" not in profile.agent_patterns.multi_agent
    assert ("consensus", "no_execution_driver") in {
        (item.strategy_id, item.reason_code) for item in profile.rejected_alternatives
    }
    # Every strategy the profile names is compilable by the local kernel.
    GraphFactory().create(profile, _services())


def test_pattern_catalogue_marks_undriven_patterns_unavailable() -> None:
    summary = summarize_pattern_selection("say hello", goal_id="g", tenant_id="t")
    by_id = {item["id"]: item for item in summary["available_patterns"]}
    assert by_id["react"]["available"] is True
    assert by_id["supervisor"]["available"] is True
    for strategy_id in ("rewoo", "lats", "codeact", "magentic", "consensus", "scratchpad"):
        assert by_id[strategy_id]["available"] is False, strategy_id


def test_distributed_profile_copy_compiles_locally_for_nodes_the_kernel_has() -> None:
    local = replace(
        _profile("supervisor", tier=ExecutionTier.DISTRIBUTED), execution_tier=ExecutionTier.LOCAL
    )
    graph = GraphFactory().create(local, _services())
    assert graph._enable_supervisor is True
