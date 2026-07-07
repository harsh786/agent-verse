"""Tests for StrategyRegistry — canonical pattern catalogue."""
from __future__ import annotations

import pytest
from app.orchestration.strategy_registry import (
    StrategyCapability,
    StrategyState,
    StrategyCategory,
    StrategyRegistry,
    build_default_registry,
)


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
    required = {
        "naive_rag",
        "hybrid_rag",
        "hyde",
        "multi_hop_rag",
        "graph_rag",
        "corrective_rag",
        "adaptive_rag",
        "agentic_rag",
        "web_augmented_rag",
        "fusion_rag",
        "self_rag",
    }
    registered = {s.strategy_id for s in registry.list_by_category(StrategyCategory.RAG)}
    assert required.issubset(registered), f"Missing: {required - registered}"


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


def test_lookup_by_id():
    registry = build_default_registry()
    cap = registry.get("hybrid_rag")
    assert cap is not None
    assert cap.strategy_id == "hybrid_rag"
    assert cap.category == StrategyCategory.RAG


def test_lookup_nonexistent_returns_none():
    assert build_default_registry().get("does_not_exist") is None


def test_filter_by_cost_class():
    registry = build_default_registry()
    low_cost = registry.filter(cost_class="low")
    assert all(s.cost_class == "low" for s in low_cost)


def test_is_available_checks_state():
    registry = build_default_registry()
    assert registry.is_available("hybrid_rag", available_deps={"knowledge_store", "embedder"})
    cap = registry.get("tree_of_thoughts")
    assert cap is not None
    assert cap.strategy_id == "tree_of_thoughts"
