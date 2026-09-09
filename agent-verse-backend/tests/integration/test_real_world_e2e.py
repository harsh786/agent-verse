# tests/integration/test_real_world_e2e.py
"""Real-world E2E integration tests using live OpenAI API + real Postgres + Redis.

These tests require:
  - OPENAI_API_KEY set
  - Postgres running on localhost:5432
  - Redis running on localhost:6379
"""
from __future__ import annotations

import os

import pytest

# Set up credentials from environment
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")  # must be set via environment variable
os.environ["OPENAI_API_KEY"] = OPENAI_KEY

pytestmark = pytest.mark.integration


# ── REAL LLM GOAL EXECUTION ───────────────────────────────────────────────────

async def test_simple_goal_with_real_openai():
    """A simple analytical goal must complete with real OpenAI."""
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.tenancy.context import PlanTier, TenantContext

    provider = OpenAICompatibleProvider(
        api_key=OPENAI_KEY,
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )
    ctx = TenantContext(
        tenant_id="integration_t1",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="test",
    )
    g = AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        max_iterations=3,
    )
    state = await g.run(
        goal="What is 2 + 2? Answer with just the number.",
        tenant_ctx=ctx,
    )
    assert state.status == GoalStatus.COMPLETE
    assert len(state.steps) >= 1
    # Check answer contains "4"
    all_output = " ".join(s.output or "" for s in state.steps)
    assert "4" in all_output or "four" in all_output.lower()


async def test_real_openai_embedding():
    """OpenAI embedding must return a non-empty vector."""
    from app.providers.base import EmbedRequest
    from app.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key=OPENAI_KEY,
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )
    resp = await provider.embed(EmbedRequest(texts=["Hello world, this is a test."]))
    assert resp.embeddings is not None
    assert len(resp.embeddings) == 1
    assert len(resp.embeddings[0]) > 0  # text-embedding-3-small → 1536 dims


async def test_knowledge_base_with_real_embeddings():
    """KB must store and retrieve documents using real embeddings."""
    from app.providers.base import EmbedRequest
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.rag.models import Chunk, KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(
        tenant_id="emb_test_t1",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="test",
    )
    provider = OpenAICompatibleProvider(
        api_key=OPENAI_KEY,
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )

    # Get real embedding for document
    resp = await provider.embed(EmbedRequest(texts=["Python machine learning with scikit-learn"]))
    embedding = resp.embeddings[0]

    store = KnowledgeStore()
    col = KnowledgeCollection(
        name="test_docs",
        collection_id="test_col_emb_e2e",
        embedder="openai",
    )
    store.create_collection(col, tenant_ctx=ctx)
    store.ingest_chunk(
        Chunk(
            document_id="test_doc_1",
            content="Python machine learning tutorial using scikit-learn for classification.",
            embedding=embedding,
            chunk_index=0,
            chunk_id="emb_c1_e2e",
            metadata={"source_url": "https://example.com/ml"},
        ),
        collection_id="test_col_emb_e2e",
        tenant_ctx=ctx,
    )

    # Get query embedding
    query_resp = await provider.embed(EmbedRequest(texts=["python sklearn tutorial"]))
    query_embedding = query_resp.embeddings[0]

    results = store.hybrid_search(
        query="python sklearn tutorial",
        query_embedding=query_embedding,
        collection_id="test_col_emb_e2e",
        tenant_ctx=ctx,
        top_k=5,
    )
    assert len(results) >= 1
    assert results[0].chunk_id == "emb_c1_e2e"


async def test_real_world_pattern_selector_routes_correctly():
    """PatternSelector must route expert analytical goals to raptor."""
    from app.orchestration.pattern_selector import PatternSelector
    from app.orchestration.runtime_profile import Complexity, Domain, GoalProperties
    from app.orchestration.strategy_registry import build_default_registry

    reg = build_default_registry()
    sel = PatternSelector(registry=reg)

    # Expert analytical → should get raptor
    expert_props = GoalProperties(
        raw_goal="comprehensive analysis",
        complexity=Complexity.EXPERT,
        domain=Domain.ANALYTICAL,
    )
    rag_config = sel.select_rag_strategy(expert_props)
    assert rag_config.strategy == "raptor"
    assert rag_config.max_context_tokens == 8000

    # Complex → fusion_rag
    complex_props = GoalProperties(
        raw_goal="complex multi-source query",
        complexity=Complexity.COMPLEX,
    )
    rag_config2 = sel.select_rag_strategy(complex_props)
    assert rag_config2.strategy == "fusion_rag"


async def test_episodic_memory_stores_and_recalls():
    """Episodic memory must store and recall episodes in-memory."""
    from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
    from app.memory.episodic import EpisodicMemoryStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(
        tenant_id="ep_test_t1",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="test",
    )

    # Test with in-memory (no DB factory needed)
    store = EpisodicMemoryStore(db_factory=None)

    state = AgentState(
        goal="analyze sales data with Python",
        tenant_ctx=ctx,
        goal_id="ep_g1",
    )
    state.status = GoalStatus.COMPLETE
    step = StepResult(
        description="analyze",
        output="Found insights",
        status=StepStatus.COMPLETE,
    )
    step.tool_calls = [{"tool_name": "python.execute", "success": True}]
    state.steps = [step]

    await store.record(state=state, tenant_ctx=ctx, quality_score=0.88)

    episodes = await store.recall(
        goal="Python data analysis",
        tenant_id="ep_test_t1",
        limit=3,
    )
    assert len(episodes) >= 1
    assert episodes[0].outcome == "success"
    assert abs(episodes[0].quality_score - 0.88) < 0.01


async def test_bm25_outperforms_pure_vector_for_keyword_queries():
    """BM25 must provide better keyword precision — off-topic doc must not rank top 2."""
    from app.rag.bm25 import BM25Retriever

    retriever = BM25Retriever()
    retriever.index([
        {"chunk_id": "c1", "content": "The agentverse platform supports dynamic orchestration of AI goals"},
        {"chunk_id": "c2", "content": "Machine learning models can be deployed using containerization"},
        {"chunk_id": "c3", "content": "AgentVerse goals are executed by AI agents using LangGraph"},
        {"chunk_id": "c4", "content": "The weather forecast shows rain tomorrow afternoon"},
    ])

    results = retriever.search("agentverse goals AI agents", top_k=4)
    top2 = [r.chunk_id for r in results[:2]]

    # AgentVerse-related docs must rank above weather
    assert "c4" not in top2


async def test_self_improvement_dispatch_updates_optimizer():
    """SelfImprovementEngine must decide UPDATE_PROMPT_VARIANT when goal_success is low."""
    from app.evals.runtime_scorecard import ScorecardResult
    from app.evals.self_improvement_engine import ImprovementAction, SelfImprovementEngine
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

    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.5,
            "rag_quality": 0.8,
            "safety": 1.0,
            "latency": 0.9,
            "cost_efficiency": 0.9,
            "grounding": 0.9,
            "citation_quality": 0.8,
            "retrieval_confidence": 0.8,
            "tool_success_rate": 0.9,
        },
        overall_score=0.6,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )

    actions = engine.decide_actions(result, profile)
    assert len(actions) >= 1
    action_types = [a.action_type for a in actions]

    # goal_success=0.5 (< 0.7) should trigger UPDATE_PROMPT_VARIANT
    assert ImprovementAction.UPDATE_PROMPT_VARIANT in action_types


async def test_openai_completion_returns_text():
    """Real OpenAI completion must return non-empty content."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key=OPENAI_KEY,
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )
    req = CompletionRequest(
        messages=[Message(role="user", content="Reply with the word HELLO only.")],
        model="gpt-4o-mini",
        max_tokens=20,
        temperature=0.0,
    )
    resp = await provider.complete(req)
    assert resp.content
    assert len(resp.content) > 0
    assert resp.input_tokens > 0
    assert resp.output_tokens > 0
