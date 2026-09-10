"""Tests for all 13 agent pattern adapters and ALL_PATTERNS list."""
from __future__ import annotations

from app.agent.patterns import (
    ALL_PATTERNS,
    AgentPattern,
    ConsensusPattern,
    DebatePattern,
    GoalTreePattern,
    LoopEngineeringPattern,
    PatternState,
    PeerReviewPattern,
    PlanExecutePattern,
    ReActPattern,
    ReflectionPattern,
    ReflexionPattern,
    SelfConsistencyPattern,
    SelfRefinePattern,
    SupervisorPattern,
    TreeOfThoughtsPattern,
)


def test_all_patterns_importable() -> None:
    """All 13 pattern classes can be imported without error."""
    patterns = [
        ReActPattern, PlanExecutePattern, ReflectionPattern, ReflexionPattern,
        SelfRefinePattern, SelfConsistencyPattern, TreeOfThoughtsPattern,
        LoopEngineeringPattern, SupervisorPattern, DebatePattern,
        GoalTreePattern, ConsensusPattern, PeerReviewPattern,
    ]
    for cls in patterns:
        instance = cls()
        assert isinstance(instance, AgentPattern)


def test_unique_pattern_ids() -> None:
    """All patterns have unique IDs."""
    ids = [p.pattern_id for p in ALL_PATTERNS]
    assert len(ids) == len(set(ids)), f"Duplicate pattern IDs: {ids}"


def test_state_validity() -> None:
    """All patterns have valid PatternState values."""
    valid_states = set(PatternState)
    for pattern in ALL_PATTERNS:
        assert pattern.state in valid_states, (
            f"{pattern.pattern_id} has invalid state: {pattern.state}"
        )


def test_react_is_implemented() -> None:
    assert ReActPattern().state == PatternState.IMPLEMENTED


def test_plan_execute_is_implemented() -> None:
    assert PlanExecutePattern().state == PatternState.IMPLEMENTED


def test_reflection_is_implemented() -> None:
    assert ReflectionPattern().state == PatternState.IMPLEMENTED


def test_tree_of_thoughts_is_implemented() -> None:
    assert TreeOfThoughtsPattern().state == PatternState.IMPLEMENTED


def test_self_consistency_is_implemented() -> None:
    assert SelfConsistencyPattern().state == PatternState.IMPLEMENTED


def test_all_patterns_has_13_entries() -> None:
    assert len(ALL_PATTERNS) == 13
