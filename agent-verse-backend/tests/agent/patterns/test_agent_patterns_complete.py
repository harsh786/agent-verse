# tests/agent/patterns/test_agent_patterns_complete.py
"""All 5 agent patterns must be IMPLEMENTED with working execute() methods."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.patterns.base import PatternState
from app.providers.fake import FakeProvider

# ── Self-Refine ───────────────────────────────────────────────────────────────

def test_self_refine_state_is_implemented():
    from app.agent.patterns.self_refine import SelfRefinePattern
    p = SelfRefinePattern()
    assert p.state == PatternState.IMPLEMENTED
    assert p.node_name == "_node_refine"


async def test_self_refine_improves_output():
    from app.agent.patterns.self_refine import SelfRefinePattern
    provider = FakeProvider(responses=["Improved: the answer is 42."])
    pattern = SelfRefinePattern()
    result = await pattern.execute(
        last_output="the answer is 42",
        task="calculate 6 * 7",
        provider=provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_self_refine_returns_original_on_no_changes():
    from app.agent.patterns.self_refine import SelfRefinePattern
    provider = FakeProvider(responses=["NO_CHANGES_NEEDED"])
    pattern = SelfRefinePattern()
    result = await pattern.execute(
        last_output="perfect answer",
        task="say hello",
        provider=provider,
    )
    assert result == "perfect answer"


async def test_self_refine_max_iterations():
    from app.agent.patterns.self_refine import SelfRefinePattern
    provider = FakeProvider(responses=["Better!", "Better again!", "Best!"])
    pattern = SelfRefinePattern(max_iterations=2)
    result = await pattern.execute(
        last_output="initial",
        task="improve",
        provider=provider,
        current_iteration=1,
    )
    # At max_iterations, should return as-is
    pattern2 = SelfRefinePattern(max_iterations=2)
    result2 = await pattern2.execute(
        last_output="final",
        task="improve",
        provider=provider,
        current_iteration=2,
    )
    assert result2 == "final"


# ── Reflexion ──────────────────────────────────────────────────────────────────

def test_reflexion_state_is_implemented():
    from app.agent.patterns.reflexion import ReflexionPattern
    p = ReflexionPattern()
    assert p.state == PatternState.IMPLEMENTED
    assert p.node_name == "_node_reflect"


async def test_reflexion_stores_lesson_on_failure():
    from app.agent.patterns.reflexion import ReflexionPattern
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    pattern = ReflexionPattern(reflexion_store=store)
    result = await pattern.store_lesson(
        tenant_id="t1",
        goal="delete prod db",
        feedback="permission denied for users table",
        source_goal_id="g1",
    )
    assert result is True
    lessons = store.recall(tenant_id="t1", limit=5)
    assert any("permission denied" in l["lesson"] for l in lessons)


async def test_reflexion_recalls_relevant_lessons():
    from app.agent.patterns.reflexion import ReflexionPattern
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="For goal 'delete db': always check permissions first",
        source_goal_id="g0",
        failure_class="auth_failure",
    )
    pattern = ReflexionPattern(reflexion_store=store)
    lessons = pattern.recall_lessons(tenant_id="t1", limit=3)
    assert len(lessons) == 1
    assert "permissions" in lessons[0]["lesson"]


async def test_reflexion_no_crash_without_store():
    from app.agent.patterns.reflexion import ReflexionPattern
    pattern = ReflexionPattern()
    # Must not raise
    result = await pattern.store_lesson(
        tenant_id="t1", goal="test", feedback="fail", source_goal_id="g1"
    )
    assert result is True


# ── Peer Review ───────────────────────────────────────────────────────────────

def test_peer_review_state_is_implemented():
    from app.agent.patterns.peer_review import PeerReviewPattern
    p = PeerReviewPattern()
    assert p.state == PatternState.IMPLEMENTED


async def test_peer_review_returns_score_and_critique():
    from app.agent.patterns.peer_review import PeerReviewPattern, PeerReviewResult
    provider = FakeProvider(responses=['{"quality_score": 0.85, "critique": "Good answer", "suggestions": ["Add more detail"], "approved": true}'])
    pattern = PeerReviewPattern()
    result = await pattern.execute(
        output="The capital of France is Paris.",
        goal="What is the capital of France?",
        provider=provider,
    )
    assert isinstance(result, PeerReviewResult)
    assert 0.0 <= result.quality_score <= 1.0
    assert isinstance(result.critique, str)
    assert isinstance(result.approved, bool)


async def test_peer_review_fallback_without_json():
    from app.agent.patterns.peer_review import PeerReviewPattern, PeerReviewResult
    provider = FakeProvider(responses=["This is a good answer."])
    pattern = PeerReviewPattern()
    result = await pattern.execute(
        output="Some answer",
        goal="Some goal",
        provider=provider,
    )
    assert isinstance(result, PeerReviewResult)
    # Must not crash even with non-JSON response


async def test_peer_review_low_quality_not_approved():
    from app.agent.patterns.peer_review import PeerReviewPattern, PeerReviewResult
    provider = FakeProvider(responses=['{"quality_score": 0.2, "critique": "Very incomplete", "suggestions": [], "approved": false}'])
    pattern = PeerReviewPattern()
    result = await pattern.execute(
        output="I don't know.",
        goal="Explain quantum computing",
        provider=provider,
    )
    assert result.approved is False
    assert result.quality_score < 0.5


# ── Self-Consistency ──────────────────────────────────────────────────────────

def test_self_consistency_state_is_implemented():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    p = SelfConsistencyPattern()
    assert p.state == PatternState.IMPLEMENTED


async def test_self_consistency_runs_n_times():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    responses = ["Answer: Paris", "Answer: Paris", "Answer: London"]
    provider = FakeProvider(responses=responses)
    pattern = SelfConsistencyPattern(n_samples=3)
    result = await pattern.execute(
        prompt="What is the capital of France?",
        provider=provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_self_consistency_majority_wins():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    # 3 out of 4 agree on "Paris"
    responses = ["Paris", "London", "Paris", "Paris"]
    provider = FakeProvider(responses=responses)
    pattern = SelfConsistencyPattern(n_samples=4)
    result = await pattern.execute(
        prompt="Capital of France?",
        provider=provider,
    )
    assert "Paris" in result


async def test_self_consistency_single_sample_fallback():
    from app.agent.patterns.self_consistency import SelfConsistencyPattern
    provider = FakeProvider(responses=["Only answer"])
    pattern = SelfConsistencyPattern(n_samples=1)
    result = await pattern.execute(
        prompt="test",
        provider=provider,
    )
    assert "Only answer" in result


# ── Tree of Thoughts ──────────────────────────────────────────────────────────

def test_tree_of_thoughts_state_is_implemented():
    from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
    p = TreeOfThoughtsPattern()
    assert p.state == PatternState.IMPLEMENTED


async def test_tree_of_thoughts_generates_thoughts():
    from app.agent.patterns.tree_of_thoughts import ThoughtNode, TreeOfThoughtsPattern
    responses = [
        # Thought generation (3 thoughts)
        "Thought 1: Start by listing all factors",
        "Thought 2: Consider edge cases first",
        "Thought 3: Break into sub-problems",
        # Evaluation (scores for 3 thoughts)
        '{"score": 0.9, "promising": true, "reason": "systematic approach"}',
        '{"score": 0.6, "promising": false, "reason": "too defensive"}',
        '{"score": 0.75, "promising": true, "reason": "good decomposition"}',
        # Expansion of best thought (1 expansion)
        "Final answer: The solution is X",
    ]
    provider = FakeProvider(responses=responses)
    pattern = TreeOfThoughtsPattern(n_thoughts=3, max_depth=1)
    result = await pattern.execute(
        problem="Solve this complex problem",
        provider=provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_tree_of_thoughts_returns_best_thought():
    from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
    responses = [
        "Thought A: good approach",
        "Thought B: better approach",
        '{"score": 0.5, "promising": true, "reason": "ok"}',
        '{"score": 0.9, "promising": true, "reason": "excellent"}',
        "Best answer from B",
    ]
    provider = FakeProvider(responses=responses)
    pattern = TreeOfThoughtsPattern(n_thoughts=2, max_depth=1)
    result = await pattern.execute(
        problem="What is 2+2?",
        provider=provider,
    )
    assert isinstance(result, str)
