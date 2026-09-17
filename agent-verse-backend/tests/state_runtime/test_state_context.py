"""StateRuntimeContext: 8 sources, StateContextBuilder, session memory clearing."""
from __future__ import annotations

import types

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
from app.state_runtime.state_context import StateContextBuilder, StateRuntimeContext
from app.tenancy.context import PlanTier, TenantContext


def _make_profile(use_ltm=True, **overrides):
    mc = MemoryCacheConfig(use_long_term_memory=use_ltm, **overrides)
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1", properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=mc, eval_config=EvalConfig(),
    )


_TENANT = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


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


# ── StateContextBuilder.build() ──────────────────────────────────────────────


class TestBuildNoStores:
    async def test_no_stores_configured_returns_empty_context(self):
        builder = StateContextBuilder()
        ctx = await builder.build("goal text", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False))
        assert ctx.execution_memory == []
        assert ctx.long_term_memory == []
        assert ctx.reflexion_lessons == []
        assert ctx.graph_facts == []
        assert ctx.semantic_cache_hits == []
        assert ctx.session_memory == []
        assert ctx.degradation_notes == []

    async def test_retrieval_chunks_and_web_results_pass_through(self):
        builder = StateContextBuilder()
        chunks = [{"content": "kb", "score": 0.5}]
        web = [{"content": "web", "url": "http://x"}]
        ctx = await builder.build(
            "goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False),
            retrieval_chunks=chunks, web_results=web,
        )
        assert ctx.knowledge_chunks == chunks
        assert ctx.web_results == web

    async def test_session_memory_always_cleared(self):
        builder = StateContextBuilder()
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False))
        assert ctx.session_memory == []


class TestExecutionMemory:
    async def test_populates_from_recall(self):
        exec_mem = types.SimpleNamespace(
            recall=lambda goal_hint, tenant_ctx, top_k: [
                {"goal": "past", "plan": ["s1", "s2"]},
            ]
        )
        builder = StateContextBuilder(execution_memory=exec_mem)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False))
        assert ctx.execution_memory == [{"goal": "past", "plan": ["s1", "s2"]}]

    async def test_recall_failure_adds_degradation_note(self):
        def _boom(**kwargs):
            raise RuntimeError("down")

        exec_mem = types.SimpleNamespace(recall=_boom)
        builder = StateContextBuilder(execution_memory=exec_mem)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False))
        assert ctx.execution_memory == []
        assert "execution_memory recall failed" in ctx.degradation_notes

    async def test_disabled_flag_skips_recall(self):
        exec_mem = types.SimpleNamespace(recall=lambda **k: [{"goal": "x", "plan": []}])
        builder = StateContextBuilder(execution_memory=exec_mem)
        profile = _make_profile(use_ltm=False, use_execution_memory=False)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.execution_memory == []


class TestLongTermMemory:
    async def test_safe_entries_included_unsafe_filtered(self):
        entries = [
            types.SimpleNamespace(content="User SSN is 123-45-6789", memory_type="fact", confidence=0.9),
            types.SimpleNamespace(content="Prefer async over sync", memory_type="lesson", confidence=0.8),
        ]
        ltm = types.SimpleNamespace(recall=lambda query, tenant_ctx, top_k: entries)
        builder = StateContextBuilder(ltm_store=ltm)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=True))
        assert len(ctx.long_term_memory) == 1
        assert ctx.long_term_memory[0]["content"] == "Prefer async over sync"

    async def test_recall_failure_adds_degradation_note(self):
        def _boom(**kwargs):
            raise RuntimeError("down")

        ltm = types.SimpleNamespace(recall=_boom)
        builder = StateContextBuilder(ltm_store=ltm)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=True))
        assert ctx.long_term_memory == []
        assert "long_term_memory recall failed" in ctx.degradation_notes

    async def test_disabled_flag_skips_recall(self):
        ltm = types.SimpleNamespace(recall=lambda **k: [])
        builder = StateContextBuilder(ltm_store=ltm)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=_make_profile(use_ltm=False))
        assert ctx.long_term_memory == []


class TestReflexion:
    async def test_populates_lessons(self):
        reflexion = types.SimpleNamespace(
            recall=lambda tenant_id, limit: [{"lesson": "always verify"}, {"lesson": "retry once"}]
        )
        builder = StateContextBuilder(reflexion_store=reflexion)
        profile = _make_profile(use_ltm=False, reflexion_enabled=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.reflexion_lessons == ["always verify", "retry once"]

    async def test_recall_failure_adds_degradation_note(self):
        def _boom(**kwargs):
            raise RuntimeError("down")

        reflexion = types.SimpleNamespace(recall=_boom)
        builder = StateContextBuilder(reflexion_store=reflexion)
        profile = _make_profile(use_ltm=False, reflexion_enabled=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.reflexion_lessons == []
        assert "reflexion_store recall failed" in ctx.degradation_notes

    async def test_disabled_flag_skips_recall(self):
        reflexion = types.SimpleNamespace(recall=lambda **k: [{"lesson": "x"}])
        builder = StateContextBuilder(reflexion_store=reflexion)
        profile = _make_profile(use_ltm=False, reflexion_enabled=False)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.reflexion_lessons == []


class TestKnowledgeGraph:
    async def test_strategy_none_skips_query(self, monkeypatch):
        import app.state_runtime.kg_query_engine as kg_mod

        class _FakeEngine:
            def __init__(self, kg_store):
                self.kg_store = kg_store

            def select_strategy(self, goal):
                return "none"

            async def query(self, goal, tenant_id, strategy):
                raise AssertionError("should not be called when strategy is none")

        monkeypatch.setattr(kg_mod, "KGQueryEngine", _FakeEngine)
        builder = StateContextBuilder(kg_store=object())
        profile = _make_profile(use_ltm=False, use_knowledge_graph=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.graph_facts == []

    async def test_strategy_found_populates_facts(self, monkeypatch):
        import app.state_runtime.kg_query_engine as kg_mod

        class _Result:
            facts = [{"entity": "A", "relation": "rel", "target": "B"}]

        class _FakeEngine:
            def __init__(self, kg_store):
                pass

            def select_strategy(self, goal):
                return "path"

            async def query(self, goal, tenant_id, strategy):
                return _Result()

        monkeypatch.setattr(kg_mod, "KGQueryEngine", _FakeEngine)
        builder = StateContextBuilder(kg_store=object())
        profile = _make_profile(use_ltm=False, use_knowledge_graph=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.graph_facts == [{"entity": "A", "relation": "rel", "target": "B"}]

    async def test_query_failure_adds_degradation_note(self, monkeypatch):
        import app.state_runtime.kg_query_engine as kg_mod

        class _FakeEngine:
            def __init__(self, kg_store):
                pass

            def select_strategy(self, goal):
                return "path"

            async def query(self, goal, tenant_id, strategy):
                raise RuntimeError("kg down")

        monkeypatch.setattr(kg_mod, "KGQueryEngine", _FakeEngine)
        builder = StateContextBuilder(kg_store=object())
        profile = _make_profile(use_ltm=False, use_knowledge_graph=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.graph_facts == []
        assert "knowledge_graph query failed" in ctx.degradation_notes

    async def test_disabled_flag_skips_kg(self):
        builder = StateContextBuilder(kg_store=object())
        profile = _make_profile(use_ltm=False, use_knowledge_graph=False)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.graph_facts == []


class TestSemanticCache:
    async def test_hit_not_stale_populates_cache_hits(self, monkeypatch):
        import app.state_runtime.cache_bridge as cache_mod

        class _Hit:
            content = "cached answer"
            similarity = 0.97
            is_stale = False

        class _FakeBridge:
            def __init__(self, semantic_cache):
                pass

            async def lookup(self, step_text, tenant_id):
                return _Hit()

        monkeypatch.setattr(cache_mod, "SemanticCacheBridge", _FakeBridge)
        builder = StateContextBuilder(semantic_cache=object())
        profile = _make_profile(use_ltm=False, use_semantic_cache=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.semantic_cache_hits == [{"content": "cached answer", "score": 0.97}]

    async def test_stale_hit_is_excluded(self, monkeypatch):
        import app.state_runtime.cache_bridge as cache_mod

        class _Hit:
            content = "stale"
            similarity = 0.9
            is_stale = True

        class _FakeBridge:
            def __init__(self, semantic_cache):
                pass

            async def lookup(self, step_text, tenant_id):
                return _Hit()

        monkeypatch.setattr(cache_mod, "SemanticCacheBridge", _FakeBridge)
        builder = StateContextBuilder(semantic_cache=object())
        profile = _make_profile(use_ltm=False, use_semantic_cache=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.semantic_cache_hits == []

    async def test_no_hit_leaves_cache_hits_empty(self, monkeypatch):
        import app.state_runtime.cache_bridge as cache_mod

        class _FakeBridge:
            def __init__(self, semantic_cache):
                pass

            async def lookup(self, step_text, tenant_id):
                return None

        monkeypatch.setattr(cache_mod, "SemanticCacheBridge", _FakeBridge)
        builder = StateContextBuilder(semantic_cache=object())
        profile = _make_profile(use_ltm=False, use_semantic_cache=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.semantic_cache_hits == []

    async def test_lookup_failure_adds_degradation_note(self, monkeypatch):
        import app.state_runtime.cache_bridge as cache_mod

        class _FakeBridge:
            def __init__(self, semantic_cache):
                pass

            async def lookup(self, step_text, tenant_id):
                raise RuntimeError("cache down")

        monkeypatch.setattr(cache_mod, "SemanticCacheBridge", _FakeBridge)
        builder = StateContextBuilder(semantic_cache=object())
        profile = _make_profile(use_ltm=False, use_semantic_cache=True)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.semantic_cache_hits == []
        assert "semantic_cache lookup failed" in ctx.degradation_notes

    async def test_disabled_flag_skips_cache(self):
        builder = StateContextBuilder(semantic_cache=object())
        profile = _make_profile(use_ltm=False, use_semantic_cache=False)
        ctx = await builder.build("goal", tenant_ctx=_TENANT, profile=profile)
        assert ctx.semantic_cache_hits == []


class TestFullBuildAllSourcesTogether:
    async def test_all_sources_combined(self, monkeypatch):
        import app.state_runtime.cache_bridge as cache_mod
        import app.state_runtime.kg_query_engine as kg_mod

        exec_mem = types.SimpleNamespace(
            recall=lambda goal_hint, tenant_ctx, top_k: [{"goal": "g", "plan": ["p"]}]
        )
        ltm = types.SimpleNamespace(
            recall=lambda query, tenant_ctx, top_k: [
                types.SimpleNamespace(content="safe fact", memory_type="fact", confidence=0.7)
            ]
        )
        reflexion = types.SimpleNamespace(recall=lambda tenant_id, limit: [{"lesson": "l1"}])

        class _KGResult:
            facts = [{"entity": "E"}]

        class _FakeEngine:
            def __init__(self, kg_store):
                pass

            def select_strategy(self, goal):
                return "auto"

            async def query(self, goal, tenant_id, strategy):
                return _KGResult()

        class _Hit:
            content = "cache hit"
            similarity = 1.0
            is_stale = False

        class _FakeBridge:
            def __init__(self, semantic_cache):
                pass

            async def lookup(self, step_text, tenant_id):
                return _Hit()

        monkeypatch.setattr(kg_mod, "KGQueryEngine", _FakeEngine)
        monkeypatch.setattr(cache_mod, "SemanticCacheBridge", _FakeBridge)

        builder = StateContextBuilder(
            execution_memory=exec_mem,
            ltm_store=ltm,
            reflexion_store=reflexion,
            kg_store=object(),
            semantic_cache=object(),
        )
        profile = _make_profile(
            use_ltm=True,
            use_execution_memory=True,
            reflexion_enabled=True,
            use_knowledge_graph=True,
            use_semantic_cache=True,
        )
        ctx = await builder.build(
            "goal", tenant_ctx=_TENANT, profile=profile,
            retrieval_chunks=[{"content": "kb"}], web_results=[{"content": "web"}],
        )
        assert ctx.execution_memory == [{"goal": "g", "plan": ["p"]}]
        assert ctx.long_term_memory == [{"content": "safe fact", "memory_type": "fact", "confidence": 0.7}]
        assert ctx.reflexion_lessons == ["l1"]
        assert ctx.graph_facts == [{"entity": "E"}]
        assert ctx.semantic_cache_hits == [{"content": "cache hit", "score": 1.0}]
        assert ctx.knowledge_chunks == [{"content": "kb"}]
        assert ctx.web_results == [{"content": "web"}]
        assert ctx.session_memory == []
        assert ctx.degradation_notes == []

        bundle = ctx.to_prompt_bundle(goal_context="goal")
        assert bundle.graph_facts == ctx.graph_facts
        assert bundle.semantic_cache_hits == ctx.semantic_cache_hits
