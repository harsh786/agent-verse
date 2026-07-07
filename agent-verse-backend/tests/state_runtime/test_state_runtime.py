"""StateRuntime: MemoryPolicyEngine, CachePolicyEngine, ReflexionStore, SessionMemory, StateRuntimeContext."""
from __future__ import annotations
import pytest
from app.state_runtime.memory_policy import MemoryPolicyEngine, MemoryDecision
from app.state_runtime.cache_policy import CachePolicyEngine, CacheDecision
from app.state_runtime.reflexion_store import ReflexionStore
from app.state_runtime.session_memory import SessionMemory
from app.state_runtime.state_context import StateRuntimeContext
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile,
    GoalProperties,
    AgentPatternConfig,
    RAGStrategyConfig,
    ModelPlanConfig,
    SecurityConfig,
    MemoryCacheConfig,
    EvalConfig,
)


def _make_profile(
    use_ltm: bool = False,
    use_semantic_cache: bool = False,
    use_session_memory: bool = True,
    reflexion_enabled: bool = False,
) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(
            use_session_memory=use_session_memory,
            use_long_term_memory=use_ltm,
            use_semantic_cache=use_semantic_cache,
            reflexion_enabled=reflexion_enabled,
        ),
        eval_config=EvalConfig(),
    )


# ── MemoryPolicyEngine ────────────────────────────────────────────────────────

def test_memory_policy_respects_ltm_flag():
    engine = MemoryPolicyEngine()
    decision = engine.decide(_make_profile(use_ltm=True))
    assert decision.use_long_term_memory is True


def test_memory_policy_ltm_off():
    engine = MemoryPolicyEngine()
    decision = engine.decide(_make_profile(use_ltm=False))
    assert decision.use_long_term_memory is False


def test_memory_policy_session_default_true():
    engine = MemoryPolicyEngine()
    decision = engine.decide(_make_profile())
    assert decision.use_session_memory is True


def test_memory_policy_session_off():
    engine = MemoryPolicyEngine()
    decision = engine.decide(_make_profile(use_session_memory=False))
    assert decision.use_session_memory is False


def test_memory_policy_reflexion_flag():
    engine = MemoryPolicyEngine()
    assert engine.decide(_make_profile(reflexion_enabled=True)).reflexion_enabled is True
    assert engine.decide(_make_profile(reflexion_enabled=False)).reflexion_enabled is False


# ── CachePolicyEngine ─────────────────────────────────────────────────────────

def test_cache_policy_allows_safe_deterministic():
    engine = CachePolicyEngine()
    profile = _make_profile(use_semantic_cache=True)
    decision = engine.decide(profile, "query", "output", is_error=False, is_nondeterministic=False)
    assert decision.should_cache is True


def test_cache_policy_blocks_error():
    engine = CachePolicyEngine()
    profile = _make_profile(use_semantic_cache=True)
    decision = engine.decide(profile, "query", "Error: failed", is_error=True, is_nondeterministic=False)
    assert decision.should_cache is False
    assert "error" in decision.reason.lower()


def test_cache_policy_blocks_nondeterministic():
    engine = CachePolicyEngine()
    profile = _make_profile(use_semantic_cache=True)
    decision = engine.decide(profile, "time", "now", is_error=False, is_nondeterministic=True)
    assert decision.should_cache is False


def test_cache_policy_off_when_flag_disabled():
    engine = CachePolicyEngine()
    profile = _make_profile(use_semantic_cache=False)
    decision = engine.decide(profile, "q", "output", is_error=False, is_nondeterministic=False)
    assert decision.should_cache is False
    assert "disabled" in decision.reason


# ── ReflexionStore ────────────────────────────────────────────────────────────

def test_reflexion_store_records_and_recalls():
    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="Use API not DB directly",
        source_goal_id="g1",
        failure_class="auth_failure",
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "API" in lessons[0]["lesson"]


def test_reflexion_store_empty_tenant():
    store = ReflexionStore()
    assert store.recall(tenant_id="nonexistent") == []


def test_reflexion_store_max_per_tenant():
    store = ReflexionStore(max_per_tenant=3)
    for i in range(5):
        store.record(
            tenant_id="t1",
            lesson=f"Lesson {i}",
            source_goal_id=f"g{i}",
            failure_class="unknown",
        )
    lessons = store.recall(tenant_id="t1", limit=10)
    assert len(lessons) <= 3


def test_reflexion_store_tenant_isolation():
    store = ReflexionStore()
    store.record(tenant_id="t1", lesson="T1 lesson", source_goal_id="g1", failure_class="x")
    store.record(tenant_id="t2", lesson="T2 lesson", source_goal_id="g2", failure_class="y")
    assert len(store.recall(tenant_id="t1")) == 1
    assert len(store.recall(tenant_id="t2")) == 1
    assert store.recall(tenant_id="t1")[0]["lesson"] == "T1 lesson"


def test_reflexion_store_limit_respected():
    store = ReflexionStore()
    for i in range(10):
        store.record(tenant_id="t1", lesson=f"L{i}", source_goal_id=f"g{i}", failure_class="x")
    assert len(store.recall(tenant_id="t1", limit=3)) == 3


# ── SessionMemory ─────────────────────────────────────────────────────────────

def test_session_memory_add_and_get():
    mem = SessionMemory()
    mem.add(goal_id="g1", key="last_tool", value="jira.search")
    items = mem.get(goal_id="g1")
    assert len(items) == 1
    assert items[0]["value"] == "jira.search"


def test_session_memory_clear():
    mem = SessionMemory()
    mem.add(goal_id="g1", key="k", value="v")
    mem.clear("g1")
    assert mem.get(goal_id="g1") == []


def test_session_memory_goal_isolation():
    mem = SessionMemory()
    mem.add(goal_id="g1", key="k1", value="v1")
    mem.add(goal_id="g2", key="k2", value="v2")
    assert len(mem.get(goal_id="g1")) == 1
    assert len(mem.get(goal_id="g2")) == 1
    mem.clear("g1")
    assert mem.get(goal_id="g1") == []
    assert len(mem.get(goal_id="g2")) == 1


def test_session_memory_multiple_entries():
    mem = SessionMemory()
    for i in range(5):
        mem.add(goal_id="g1", key=f"k{i}", value=i)
    assert len(mem.get(goal_id="g1")) == 5


def test_session_memory_empty_goal():
    mem = SessionMemory()
    assert mem.get(goal_id="unknown") == []


# ── StateRuntimeContext ───────────────────────────────────────────────────────

def test_state_runtime_context_to_prompt_bundle():
    ctx = StateRuntimeContext(
        knowledge_chunks=[{"content": "KB chunk", "score": 0.9}],
        reflexion_lessons=["lesson 1"],
        session_memory=[{"key": "k", "value": "v"}],
    )
    bundle = ctx.to_prompt_bundle("explain orchestration")
    assert bundle.goal_context == "explain orchestration"
    assert len(bundle.knowledge_chunks) == 1
    assert len(bundle.reflexion_lessons) == 1
    assert len(bundle.session_memory) == 1


def test_state_runtime_context_defaults_empty():
    ctx = StateRuntimeContext()
    assert ctx.session_memory == []
    assert ctx.execution_memory == []
    assert ctx.long_term_memory == []
    assert ctx.knowledge_chunks == []
    assert ctx.reflexion_lessons == []
    assert ctx.degradation_notes == []


def test_state_runtime_context_web_results_in_bundle():
    ctx = StateRuntimeContext(
        web_results=[{"title": "Result", "snippet": "text"}],
    )
    bundle = ctx.to_prompt_bundle("web query")
    assert len(bundle.web_results) == 1


def test_state_runtime_context_degradation_notes():
    ctx = StateRuntimeContext()
    ctx.degradation_notes.append("execution_memory recall failed")
    bundle = ctx.to_prompt_bundle("goal")
    assert len(bundle.degradation_notes) == 1
