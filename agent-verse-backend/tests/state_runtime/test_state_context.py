"""StateRuntimeContext: 8 sources, StateContextBuilder, session memory clearing."""
from __future__ import annotations

from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
)
from app.state_runtime.state_context import StateRuntimeContext


def _make_profile(use_ltm=True):
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1", properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(use_long_term_memory=use_ltm), eval_config=EvalConfig(),
    )


def test_state_context_all_9_fields():
    ctx = StateRuntimeContext(
        session_memory=[{"key": "last_tool", "value": "jira.search"}],
        execution_memory=[{"goal": "past goal", "plan": ["step1"]}],
        long_term_memory=[{"content": "lesson", "confidence": 0.9}],
        semantic_cache_hits=[{"content": "cached", "score": 0.95}],
        knowledge_chunks=[{"content": "KB chunk", "score": 0.8}],
        graph_facts=[{"entity": "AgentVerse", "relation": "supports", "target": "RAG"}],
        web_results=[{"content": "web snippet", "url": "https://news.example.com"}],
        reflexion_lessons=["lesson 1"],
        degradation_notes=["note 1"],
    )
    assert len(ctx.session_memory) == 1
    assert len(ctx.knowledge_chunks) == 1
    assert len(ctx.graph_facts) == 1
    assert len(ctx.reflexion_lessons) == 1


def test_state_context_to_prompt_bundle():
    from app.context.prompt_builder import PromptContextBundle
    ctx = StateRuntimeContext(
        session_memory=[{"key": "k", "value": "v"}],
        knowledge_chunks=[{"content": "KB chunk", "score": 0.9}],
        reflexion_lessons=["lesson 1"],
    )
    bundle = ctx.to_prompt_bundle(goal_context="test goal")
    assert isinstance(bundle, PromptContextBundle)
    assert bundle.goal_context == "test goal"
    assert len(bundle.knowledge_chunks) == 1
    assert len(bundle.reflexion_lessons) == 1


def test_session_memory_cleared_between_goals():
    from app.state_runtime.session_memory import SessionMemory
    mem = SessionMemory()
    mem.add(goal_id="g1", key="tool_used", value="jira.search")
    mem.clear("g1")
    assert mem.get(goal_id="g1") == []
    assert mem.get(goal_id="g2") == []


def test_ltm_classified_before_injection():
    from app.data_classification.classifier import DataClassifier
    classifier = DataClassifier()
    ltm_entries = [
        {"content": "User SSN is 123-45-6789", "confidence": 0.9},  # PII — blocked
        {"content": "Prefer semantic search over lexical", "confidence": 0.8},  # safe
    ]
    safe_entries = [e for e in ltm_entries if classifier.classify(e["content"]).safe_for_prompt]
    assert len(safe_entries) == 1
    assert "SSN" not in safe_entries[0]["content"]
