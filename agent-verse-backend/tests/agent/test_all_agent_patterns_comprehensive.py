"""Comprehensive tests for all agent patterns (30+ tests).

Tests every pattern: init, execute, error handling, max_iterations,
DynamicGraphAssembler, PatternAssembler, PatternSelector.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.providers.fake import FakeProvider

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake(responses: list[str] | None = None) -> FakeProvider:
    return FakeProvider(responses=responses or ["Test response."])


# ─────────────────────────────────────────────────────────────────────────────
# SelfRefinePattern
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfRefinePattern:
    def test_init_default(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        p = SelfRefinePattern()
        assert p.pattern_id == "self_refine"
        assert p._max_iterations == 2

    def test_init_custom_iterations(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        p = SelfRefinePattern(max_iterations=5)
        assert p._max_iterations == 5

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.self_refine import SelfRefinePattern
        p = SelfRefinePattern()
        assert p.state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        assert SelfRefinePattern().node_name == "_node_refine"

    async def test_execute_returns_refined(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        provider = _fake(["Improved output here."])
        p = SelfRefinePattern(max_iterations=2)
        result = await p.execute(last_output="Original", task="Write essay", provider=provider)
        assert result == "Improved output here."

    async def test_execute_no_changes_needed(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        provider = _fake(["NO_CHANGES_NEEDED"])
        p = SelfRefinePattern(max_iterations=2)
        result = await p.execute(last_output="Original", task="Write essay", provider=provider)
        assert result == "Original"

    async def test_execute_at_max_iterations(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        provider = _fake(["Should not be called"])
        p = SelfRefinePattern(max_iterations=2)
        result = await p.execute(
            last_output="Final output", task="task", provider=provider, current_iteration=2
        )
        assert result == "Final output"
        # Provider should not have been called
        assert len(provider.call_history) == 0

    async def test_execute_provider_error_returns_original(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("boom"))
        p = SelfRefinePattern(max_iterations=2)
        result = await p.execute(last_output="Original", task="task", provider=provider)
        assert result == "Original"

    def test_is_compatible(self) -> None:
        from app.agent.patterns.self_refine import SelfRefinePattern
        assert SelfRefinePattern().is_compatible(None) is True


# ─────────────────────────────────────────────────────────────────────────────
# ReflexionPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestReflexionPattern:
    def test_init(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        p = ReflexionPattern()
        assert p.pattern_id == "reflexion"

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.reflexion import ReflexionPattern
        assert ReflexionPattern().state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        assert ReflexionPattern().node_name == "_node_reflect"

    async def test_store_and_recall_lesson(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        p = ReflexionPattern(reflexion_store=store)
        result = await p.store_lesson(
            tenant_id="t1",
            goal="Build a pipeline",
            feedback="Always check env vars first",
            source_goal_id="g1",
        )
        assert result is True
        lessons = p.recall_lessons(tenant_id="t1", limit=5)
        assert len(lessons) == 1
        assert "env vars" in lessons[0]["lesson"]

    async def test_store_empty_feedback_returns_false(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        p = ReflexionPattern()
        result = await p.store_lesson(
            tenant_id="t1", goal="goal", feedback="", source_goal_id="g1"
        )
        assert result is False

    def test_format_for_context_empty(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        p = ReflexionPattern()
        assert p.format_for_context([]) == ""

    def test_format_for_context_with_lessons(self) -> None:
        from app.agent.patterns.reflexion import ReflexionPattern
        p = ReflexionPattern()
        lessons = [{"lesson": "Avoid X", "failure_class": "tool_error"}]
        result = p.format_for_context(lessons)
        assert "Reflexion" in result
        assert "Avoid X" in result


# ─────────────────────────────────────────────────────────────────────────────
# PeerReviewPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestPeerReviewPattern:
    def test_init(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewPattern
        p = PeerReviewPattern()
        assert p.pattern_id == "peer_review"
        assert p._threshold == 0.7

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.peer_review import PeerReviewPattern
        assert PeerReviewPattern().state == PatternState.IMPLEMENTED

    async def test_execute_returns_peer_review_result(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewPattern
        response_json = json.dumps({
            "quality_score": 0.9,
            "critique": "Excellent work",
            "suggestions": [],
            "approved": True,
        })
        provider = _fake([response_json])
        p = PeerReviewPattern()
        result = await p.execute(output="Some output", goal="Write a report", provider=provider)
        assert result.quality_score >= 0.9
        assert result.approved is True

    async def test_execute_provider_error_returns_fallback(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("timeout"))
        p = PeerReviewPattern()
        result = await p.execute(output="output", goal="goal", provider=provider)
        assert result.quality_score == 0.5
        assert result.approved is False

    def test_peer_review_result_from_dict(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewResult
        r = PeerReviewResult.from_dict({"quality_score": 0.8, "critique": "Good", "approved": True})
        assert r.quality_score == 0.8
        assert r.approved is True

    def test_peer_review_result_from_raw_json(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewResult
        raw = json.dumps({"quality_score": 0.6, "critique": "Needs work"})
        r = PeerReviewResult.from_raw(raw)
        assert r.quality_score == 0.6

    def test_peer_review_result_from_raw_text_excellent(self) -> None:
        from app.agent.patterns.peer_review import PeerReviewResult
        r = PeerReviewResult.from_raw("This is excellent work, perfect!")
        assert r.quality_score >= 0.7


# ─────────────────────────────────────────────────────────────────────────────
# SelfConsistencyPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfConsistencyPattern:
    def test_init(self) -> None:
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        p = SelfConsistencyPattern(n_samples=3, temperature=0.7)
        assert p.pattern_id == "self_consistency"
        assert p._n == 3

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        assert SelfConsistencyPattern().state == PatternState.IMPLEMENTED

    async def test_execute_returns_majority_vote(self) -> None:
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        # 3 responses, 2 say "Paris"
        provider = _fake(["Paris", "Paris", "London"])
        p = SelfConsistencyPattern(n_samples=3)
        result = await p.execute(prompt="Capital of France?", provider=provider)
        assert "Paris" in result

    async def test_execute_handles_provider_errors(self) -> None:
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = SelfConsistencyPattern(n_samples=2)
        result = await p.execute(prompt="What?", provider=provider)
        assert result == ""

    def test_vote_most_common(self) -> None:
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        p = SelfConsistencyPattern()
        result = p.vote(["A", "B", "A", "C", "A"])
        assert result == "A"

    def test_vote_single_response(self) -> None:
        from app.agent.patterns.self_consistency import SelfConsistencyPattern
        p = SelfConsistencyPattern()
        assert p.vote(["Only option"]) == "Only option"


# ─────────────────────────────────────────────────────────────────────────────
# TreeOfThoughtsPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestTreeOfThoughtsPattern:
    def test_init(self) -> None:
        from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
        p = TreeOfThoughtsPattern(n_thoughts=3, max_depth=2, beam_width=2)
        assert p.pattern_id == "tree_of_thoughts"
        assert p._n == 3

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
        assert TreeOfThoughtsPattern().state == PatternState.IMPLEMENTED

    async def test_execute_returns_string(self) -> None:
        from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
        eval_json = json.dumps({"score": 0.8, "promising": True, "reason": "good"})
        provider = _fake(["Thought A", eval_json, "Expanded answer", "Final answer"])
        p = TreeOfThoughtsPattern(n_thoughts=1, max_depth=1, beam_width=1)
        result = await p.execute(problem="Solve X", provider=provider)
        assert isinstance(result, str)

    async def test_execute_empty_thoughts_falls_back(self) -> None:
        from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = TreeOfThoughtsPattern(n_thoughts=1)
        result = await p.execute(problem="Solve X", provider=provider)
        assert result == ""

    def test_thought_node_cumulative_score(self) -> None:
        from app.agent.patterns.tree_of_thoughts import ThoughtNode
        node = ThoughtNode(content="test", score=0.9)
        assert node.cumulative_score() == 0.9


# ─────────────────────────────────────────────────────────────────────────────
# ReActPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestReActPattern:
    def test_pattern_id(self) -> None:
        from app.agent.patterns.react import ReActPattern
        assert ReActPattern().pattern_id == "react"

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.react import ReActPattern
        assert ReActPattern().state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.react import ReActPattern
        assert ReActPattern().node_name == "execute"

    def test_is_compatible(self) -> None:
        from app.agent.patterns.react import ReActPattern
        assert ReActPattern().is_compatible({}) is True

    def test_description_non_empty(self) -> None:
        from app.agent.patterns.react import ReActPattern
        assert len(ReActPattern().description) > 0


# ─────────────────────────────────────────────────────────────────────────────
# PlanExecutePattern
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanExecutePattern:
    def test_pattern_id(self) -> None:
        from app.agent.patterns.plan_execute import PlanExecutePattern
        assert PlanExecutePattern().pattern_id == "plan_execute"

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.plan_execute import PlanExecutePattern
        assert PlanExecutePattern().state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.plan_execute import PlanExecutePattern
        assert PlanExecutePattern().node_name == "plan"


# ─────────────────────────────────────────────────────────────────────────────
# DebatePattern
# ─────────────────────────────────────────────────────────────────────────────

class TestDebatePattern:
    def test_pattern_id(self) -> None:
        from app.agent.patterns.debate import DebatePattern
        assert DebatePattern().pattern_id == "debate"

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.debate import DebatePattern
        assert DebatePattern().state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.debate import DebatePattern
        assert DebatePattern().node_name == "debate"


# ─────────────────────────────────────────────────────────────────────────────
# SupervisorPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestSupervisorPattern:
    def test_pattern_id(self) -> None:
        from app.agent.patterns.supervisor import SupervisorPattern
        assert SupervisorPattern().pattern_id == "supervisor"

    def test_state_is_implemented(self) -> None:
        from app.agent.patterns.base import PatternState
        from app.agent.patterns.supervisor import SupervisorPattern
        assert SupervisorPattern().state == PatternState.IMPLEMENTED

    def test_node_name(self) -> None:
        from app.agent.patterns.supervisor import SupervisorPattern
        assert SupervisorPattern().node_name == "supervisor"


# ─────────────────────────────────────────────────────────────────────────────
# DynamicGraphAssembler
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicGraphAssembler:
    def test_import_via_patterns_module(self) -> None:
        from app.agent.patterns.dynamic_graph_assembler import DynamicGraphAssembler
        assert DynamicGraphAssembler is not None

    def test_assemble_builds_graph(self) -> None:
        from app.agent.dynamic_graph import DynamicGraphAssembler
        from app.agent.pattern_config import PatternConfig
        provider = _fake()
        config = PatternConfig(
            reasoning_patterns=["react", "chain_of_thought"],
            rag_patterns=["hybrid_rag"],
            multi_agent_patterns=["single_agent"],
            safety_patterns=["guardrails"],
        )
        assembler = DynamicGraphAssembler()
        graph = assembler.assemble(
            config, planner=provider, executor=provider, verifier=provider
        )
        # Graph must be returned (not None)
        assert graph is not None

    def test_get_active_nodes_default(self) -> None:
        from app.agent.dynamic_graph import DynamicGraphAssembler
        from app.agent.pattern_config import PatternConfig
        config = PatternConfig(
            reasoning_patterns=["react"],
            rag_patterns=["hybrid_rag"],
            multi_agent_patterns=["single_agent"],
            safety_patterns=["guardrails"],
        )
        nodes = DynamicGraphAssembler().get_active_nodes(config)
        assert "plan" in nodes
        assert "execute" in nodes

    def test_get_active_nodes_with_cot(self) -> None:
        from app.agent.dynamic_graph import DynamicGraphAssembler
        from app.agent.pattern_config import PatternConfig
        config = PatternConfig(
            reasoning_patterns=["react", "chain_of_thought"],
            rag_patterns=["hybrid_rag"],
            multi_agent_patterns=["single_agent"],
            safety_patterns=[],
        )
        nodes = DynamicGraphAssembler().get_active_nodes(config)
        assert "think" in nodes

    def test_wire_edges_returns_dict(self) -> None:
        from app.agent.dynamic_graph import DynamicGraphAssembler
        from app.agent.pattern_config import PatternConfig
        config = PatternConfig(
            reasoning_patterns=["react"],
            rag_patterns=["hybrid_rag"],
            multi_agent_patterns=["single_agent"],
            safety_patterns=[],
        )
        edges = DynamicGraphAssembler()._wire_edges(config)
        assert isinstance(edges, dict)
        assert "plan" in edges


# ─────────────────────────────────────────────────────────────────────────────
# PatternAssembler
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternAssembler:
    def test_assemble_simple_goal(self) -> None:
        from app.agent.pattern_assembler import PatternAssembler
        from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel
        props = GoalProperties(
            complexity=Complexity.SIMPLE,
            domain=Domain.OPERATIONAL,
            risk=RiskLevel.LOW,
        )
        config = PatternAssembler().assemble(props, {})
        assert "react" in config.reasoning_patterns
        assert config.max_iterations <= 25

    def test_assemble_expert_adds_cot(self) -> None:
        from app.agent.pattern_assembler import PatternAssembler
        from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel
        props = GoalProperties(
            complexity=Complexity.EXPERT,
            domain=Domain.TECHNICAL,
            risk=RiskLevel.LOW,
        )
        config = PatternAssembler().assemble(props, {})
        assert "chain_of_thought" in config.reasoning_patterns
        assert config.max_iterations >= 25

    def test_assemble_critical_risk_adds_hitl(self) -> None:
        from app.agent.pattern_assembler import PatternAssembler
        from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel
        props = GoalProperties(
            complexity=Complexity.SIMPLE,
            domain=Domain.OPERATIONAL,
            risk=RiskLevel.CRITICAL,
        )
        config = PatternAssembler().assemble(props, {})
        assert "hitl" in config.safety_patterns

    def test_assemble_always_starts_with_react(self) -> None:
        from app.agent.pattern_assembler import PatternAssembler
        from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel
        props = GoalProperties(
            complexity=Complexity.COMPLEX,
            domain=Domain.ANALYTICAL,
            risk=RiskLevel.LOW,
        )
        config = PatternAssembler().assemble(props, {})
        assert config.reasoning_patterns[0] == "react"


# ─────────────────────────────────────────────────────────────────────────────
# PatternSelector
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternSelector:
    def _make_selector(self):  # type: ignore[return]
        from app.orchestration.pattern_selector import PatternSelector
        from app.orchestration.strategy_registry import build_default_registry
        return PatternSelector(registry=build_default_registry())

    def test_select_rag_strategy_web_returns_flare(self) -> None:
        from app.orchestration.runtime_profile import GoalProperties
        selector = self._make_selector()
        props = GoalProperties(
            raw_goal="What happened today?",
            requires_web=True,
        )
        config = selector.select_rag_strategy(props)
        assert config.strategy in ("flare", "raptor", "colbert_late_interaction")

    def test_select_rag_strategy_code_returns_colbert(self) -> None:
        from app.orchestration.runtime_profile import GoalProperties
        selector = self._make_selector()
        props = GoalProperties(
            raw_goal="Write Python code",
            requires_code=True,
        )
        config = selector.select_rag_strategy(props)
        assert config.strategy == "colbert"

    def test_select_rag_strategy_expert_returns_raptor(self) -> None:
        from app.orchestration.runtime_profile import Complexity, GoalProperties
        selector = self._make_selector()
        props = GoalProperties(
            raw_goal="Expert analysis",
            complexity=Complexity.EXPERT,
        )
        config = selector.select_rag_strategy(props)
        assert config.strategy == "raptor"

    def test_select_agent_patterns_expert_adds_reflection(self) -> None:
        from app.orchestration.runtime_profile import Complexity, GoalProperties
        selector = self._make_selector()
        props = GoalProperties(
            raw_goal="Solve a hard problem",
            complexity=Complexity.EXPERT,
        )
        config = selector.select_agent_patterns(props)
        assert "reflection" in config.reasoning

    def test_select_agent_patterns_high_risk_adds_hitl(self) -> None:
        from app.orchestration.runtime_profile import GoalProperties, RiskLevel
        selector = self._make_selector()
        props = GoalProperties(
            raw_goal="Deploy to prod",
            risk=RiskLevel.HIGH,
        )
        config = selector.select_agent_patterns(props)
        assert "hitl" in config.safety


# ─────────────────────────────────────────────────────────────────────────────
# Base AgentPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentPatternBase:
    def test_base_defaults(self) -> None:
        from app.agent.patterns.base import AgentPattern, PatternState

        class _Concrete(AgentPattern):
            @property
            def pattern_id(self) -> str:
                return "test"

        p = _Concrete()
        assert p.state == PatternState.PLANNED
        assert p.description == ""
        assert p.node_name == ""
        assert p.get_node_config(None) == {}
        assert p.is_compatible(None) is True
