from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration.graph_factory import GraphFactory
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
from app.providers.fake import FakeProvider


def profile(*strategies: str) -> GoalRuntimeProfile:
    primary, *auxiliary = strategies or ("react",)
    return GoalRuntimeProfile(
        goal_id="goal-1",
        tenant_id="tenant-1",
        properties=GoalProperties(raw_goal="goal"),
        agent_patterns=AgentPatternConfig(reasoning=list(strategies or ("react",))),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection(primary, "1.0.0"),
        auxiliary_strategies=tuple(
            StrategySelection(item, "1.0.0") for item in auxiliary
        ),
    )


def services() -> dict[str, object]:
    provider = FakeProvider()
    return {"planner": provider, "executor": provider, "verifier": provider}


def test_graph_factory_requires_valid_profile_v2() -> None:
    factory = GraphFactory()
    with pytest.raises(ValueError, match="runtime profile is required"):
        factory.create(None, services())

    invalid = profile("react")
    object.__setattr__(invalid, "profile_version", 1)
    with pytest.raises(ValueError, match="profile_version must be 2"):
        factory.create(invalid, services())


def test_compiled_nodes_exactly_reflect_profile_reasoning() -> None:
    graph = GraphFactory().create(
        profile("react", "chain_of_thought", "reflection", "self_refine"),
        services(),
    )
    nodes = set(graph._graph.get_graph().nodes)

    assert {"think", "reflect", "refine"} <= nodes
    assert "debate" not in nodes
    assert "supervisor" not in nodes


def test_all_selected_existing_reasoning_patterns_compile_before_run() -> None:
    graph = GraphFactory().create(
        profile(
            "react",
            "self_refine",
            "self_consistency",
            "tree_of_thoughts",
            "peer_review",
            "reflection",
        ),
        services(),
    )

    nodes = set(graph._graph.get_graph().nodes)
    assert {"refine", "self_consistency", "tree_of_thoughts", "peer_review", "reflect"} <= nodes


def test_distributed_primary_is_not_compiled_as_local_hidden_flag() -> None:
    distributed = replace(
        profile("debate"),
        execution_tier=ExecutionTier.DISTRIBUTED,
    )

    with pytest.raises(ValueError, match="distributed strategy requires StrategyRunner"):
        GraphFactory().create(distributed, services())


def test_two_goal_profiles_compile_independently_of_insertion_order() -> None:
    first_profile = profile("react", "reflection")
    second_profile = replace(
        profile("react", "self_refine"),
        goal_id="goal-2",
    )
    factory = GraphFactory()

    second = factory.create(second_profile, services())
    first = factory.create(first_profile, services())
    first_nodes = set(first._graph.get_graph().nodes)
    second_nodes = set(second._graph.get_graph().nodes)

    assert "reflect" in first_nodes and "refine" not in first_nodes
    assert "refine" in second_nodes and "reflect" not in second_nodes
    assert first.runtime_profile.goal_id == "goal-1"
    assert second.runtime_profile.goal_id == "goal-2"


@pytest.mark.asyncio
async def test_initialize_uses_existing_profile_without_rebuilding(monkeypatch) -> None:
    graph = GraphFactory().create(profile("react"), services())

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("profile builder must not run inside initialize")

    monkeypatch.setattr(
        "app.orchestration.runtime_profile_builder.RuntimeProfileBuilder",
        forbidden,
    )
    assert graph.runtime_profile.goal_id == "goal-1"
