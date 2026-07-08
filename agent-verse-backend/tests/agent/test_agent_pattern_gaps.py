"""Agent pattern gaps from doc-1 §3.4, doc-3 §11 must all be addressed."""
from __future__ import annotations

import pytest
from app.agent.patterns.self_refine import SelfRefinePattern
from app.agent.patterns.base import PatternState
from app.agent.patterns.peer_review import PeerReviewPattern
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
from app.agent.patterns import ALL_PATTERNS


def test_self_refine_pattern_has_node_reference():
    p = SelfRefinePattern()
    assert p.pattern_id == "self_refine"
    assert p.state in (PatternState.PARTIAL, PatternState.IMPLEMENTED)
    assert "_node_refine" in p.node_name or "refine" in p.description.lower()


def test_self_refine_system_prompt_exists():
    from app.agent.prompts import SELF_REFINE_SYSTEM
    assert isinstance(SELF_REFINE_SYSTEM, str)
    assert len(SELF_REFINE_SYSTEM) > 50
    assert "refine" in SELF_REFINE_SYSTEM.lower() or "improve" in SELF_REFINE_SYSTEM.lower()


def test_peer_review_pattern_importable():
    p = PeerReviewPattern()
    assert p.pattern_id == "peer_review"


def test_peer_review_in_all_patterns():
    ids = [p.pattern_id for p in ALL_PATTERNS]
    assert "peer_review" in ids


def test_peer_review_in_strategy_registry():
    from app.orchestration.strategy_registry import build_default_registry
    registry = build_default_registry()
    cap = registry.get("peer_review")
    assert cap is not None


def test_self_consistency_pattern_exists():
    p = SelfConsistencyPattern()
    assert p.pattern_id == "self_consistency"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL, PatternState.IMPLEMENTED)


def test_tree_of_thoughts_pattern_exists():
    p = TreeOfThoughtsPattern()
    assert p.pattern_id == "tree_of_thoughts"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL, PatternState.IMPLEMENTED)


def test_all_doc1_agent_patterns_in_all_patterns_list():
    ids = {p.pattern_id for p in ALL_PATTERNS}
    required = {"react", "plan_execute", "reflection", "reflexion", "self_refine",
                "self_consistency", "tree_of_thoughts", "loop_engineering",
                "supervisor", "debate", "goal_tree", "consensus", "peer_review"}
    missing = required - ids
    assert not missing, f"Missing from ALL_PATTERNS: {sorted(missing)}"


def test_all_patterns_have_pattern_id():
    for p in ALL_PATTERNS:
        assert p.pattern_id, f"{type(p).__name__} missing pattern_id"
