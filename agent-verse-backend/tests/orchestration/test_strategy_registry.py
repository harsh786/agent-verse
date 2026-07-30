"""Tests for StrategyRegistry — canonical pattern catalogue."""

from __future__ import annotations

import enum

import pytest

from app.orchestration.strategy_registry import (
    StrategyCategory,
    StrategyState,
    build_default_registry,
)
from app.rag.contracts import RAGStrategy, resolve_rag_strategy


def test_registry_has_all_required_agent_patterns():
    registry = build_default_registry()
    required = {
        "react",
        "plan_execute",
        "reflection",
        "reflexion",
        "self_refine",
        "self_consistency",
        "tree_of_thoughts",
        "supervisor",
        "debate",
        "goal_tree",
        "consensus",
        "chain_of_thought",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.AGENT)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_all_required_rag_patterns():
    registry = build_default_registry()
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}

    assert registered == {strategy.value for strategy in RAGStrategy}


def test_registry_has_all_safety_patterns():
    registry = build_default_registry()
    required = {
        "guardrails",
        "hitl",
        "consensus_verification",
        "sandbox",
        "plan_verification",
        "data_classification",
        "provenance_verification",
        "grounding_checker",
        "circuit_breaker",
        "rollback",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.SAFETY)}
    assert required.issubset(registered), f"Missing: {required - registered}"


def test_registry_has_90_plus_entries():
    assert len(build_default_registry().list_all()) >= 90


def test_every_entry_has_valid_state():
    valid_states = {
        StrategyState.IMPLEMENTED,
        StrategyState.PARTIAL,
        StrategyState.PLANNED,
        StrategyState.DISABLED,
    }
    for entry in build_default_registry().list_all():
        assert entry.state in valid_states


def test_registry_enums_preserve_original_string_enum_bases() -> None:
    assert StrategyState.__bases__ == (str, enum.Enum)
    assert StrategyCategory.__bases__ == (str, enum.Enum)


def test_lookup_by_id():
    registry = build_default_registry()
    cap = registry.get("hybrid")
    assert cap is not None
    assert cap.strategy_id == "hybrid"
    assert cap.category == StrategyCategory.RAG


@pytest.mark.parametrize(
    ("historical_id", "canonical"),
    [
        ("fusion_rag", RAGStrategy.FUSION),
        ("corrective_rag", RAGStrategy.CORRECTIVE),
        ("speculative_rag", RAGStrategy.SPECULATIVE),
        ("colbert_late_interaction", RAGStrategy.COLBERT),
        ("multi_hop_rag", RAGStrategy.MULTI_HOP),
        ("graph_rag", RAGStrategy.GRAPH),
    ],
)
def test_only_boundary_resolver_accepts_historical_ids(
    historical_id: str,
    canonical: RAGStrategy,
) -> None:
    registry = build_default_registry()

    assert resolve_rag_strategy(historical_id) is canonical
    assert registry.get(historical_id) is None
    capability = registry.get(canonical.value)
    assert capability is not None
    assert capability.strategy_id == canonical.value


def test_lookup_nonexistent_returns_none():
    assert build_default_registry().get("does_not_exist") is None


def test_filter_by_cost_class():
    registry = build_default_registry()
    low_cost = registry.filter(cost_class="low")
    assert all(s.cost_class == "low" for s in low_cost)


def test_is_available_checks_state():
    registry = build_default_registry()
    assert not registry.is_available(
        "hybrid",
        available_deps={"knowledge_store", "embedder"},
    )
    assert registry.is_available("chain_of_thought")
    cap = registry.get("tree_of_thoughts")
    assert cap is not None
    assert cap.strategy_id == "tree_of_thoughts"
