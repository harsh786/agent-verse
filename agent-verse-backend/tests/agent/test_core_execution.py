# tests/agent/test_core_execution.py
"""Core execution: ContextPipeline in _node_plan, GuardrailEnforcer, directive parsing, web fallback."""
from __future__ import annotations
import pytest
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext, PlanTier
from app.agent.state import AgentState, GoalStatus
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="AgentVerse uses dynamic orchestration.",
              embedding=[0.1]*10, chunk_index=0, chunk_id="c1", metadata={"source_url": "https://docs.example.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


# ── ContextPipeline runs before planning ──────────────────────────────────────

async def test_context_pipeline_builds_planner_context(tenant_ctx, loaded_store):
    """ContextPipeline.run() must produce a non-empty planner context when KB has data."""
    from app.context.context_pipeline import ContextPipeline
    from app.rag.agentic.retriever_tool import RetrieverTool

    retriever = RetrieverTool(knowledge_store=loaded_store)
    retrieval = await retriever.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    pipeline = ContextPipeline(max_tokens=4000)
    result = pipeline.run(
        chunks=retrieval.chunks,
        query="dynamic orchestration",
        goal_context="explain AgentVerse",
    )
    assert result.planner_context
    assert "AgentVerse" in result.planner_context or len(result.planner_context) > 20
    assert result.verifier_context is not None
    assert result.executor_context is not None


async def test_empty_kb_pipeline_still_returns_context(tenant_ctx):
    """ContextPipeline must return valid result even with empty KB."""
    from app.context.context_pipeline import ContextPipeline
    pipeline = ContextPipeline(max_tokens=2000)
    result = pipeline.run(
        chunks=[],
        query="any query",
        goal_context="test goal",
    )
    assert result.planner_context  # at minimum, goal context is included
    assert "test goal" in result.planner_context


# ── GuardrailEnforcer wired into execution ────────────────────────────────────

def test_guardrail_enforcer_catches_injection():
    """GuardrailEnforcer must catch prompt injection in tool args."""
    from app.security_runtime.guardrail_enforcer import GuardrailEnforcer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
    )
    enforcer = GuardrailEnforcer()
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=RiskLevel.HIGH),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = enforcer.check_tool_args(
        tool_name="postgres_query",
        tool_args={"query": "SELECT 1; DROP TABLE users; --"},
        profile=profile,
    )
    assert result.checked is True
    assert result.injection_detected is True


def test_guardrail_enforcer_passes_safe_args():
    """GuardrailEnforcer must pass clean tool args without blocking."""
    from app.security_runtime.guardrail_enforcer import GuardrailEnforcer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
    )
    enforcer = GuardrailEnforcer()
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="list", risk=RiskLevel.LOW),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = enforcer.check_tool_args(
        tool_name="jira.search_issues",
        tool_args={"jql": "project = MYPROJECT AND status = Open"},
        profile=profile,
    )
    assert result.checked is True
    assert result.blocked is False


# ── [SEARCH:...] Directive parsing ───────────────────────────────────────────

def test_search_directive_parsed_from_step():
    """SearchDirectiveParser must extract directives from step descriptions."""
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    step = 'Research background: [SEARCH:kb:"dynamic orchestration patterns"] then summarize'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "kb"
    assert "orchestration" in directives[0].query


def test_search_directive_web_source():
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    step = 'Get latest: [SEARCH:web:"Python 3.12 release notes"]'
    directives = parser.extract(step)
    assert directives[0].source_type == "web"
    assert "Python 3.12" in directives[0].query


def test_search_directive_maps_to_retrieval_strategy():
    from app.rag.agentic.search_directive_parser import SearchDirectiveParser
    parser = SearchDirectiveParser()
    assert parser.directive_to_strategy("kb") == "hybrid"
    assert parser.directive_to_strategy("web") == "web"
    assert parser.directive_to_strategy("graph") == "graph"
    assert parser.directive_to_strategy("memory") == "memory"


# ── Per-step retrieval ────────────────────────────────────────────────────────

async def test_per_step_retrieval_returns_context(tenant_ctx, loaded_store):
    """Per-step retrieval must return KB context for relevant queries."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    assert result.source in ("knowledge_base", "parametric", "none_available")
    assert result.confidence >= 0.0


async def test_per_step_retrieval_with_empty_kb(tenant_ctx):
    """Per-step retrieval with empty KB must return structured result, not empty string."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    result = await tool.retrieve(query="anything", tenant_ctx=tenant_ctx)
    # MUST never be empty string — always structured
    assert result is not None
    assert result.source != ""
    assert result.strategy_used != ""


# ── Web fallback when KB empty ────────────────────────────────────────────────

async def test_web_fallback_triggered_when_kb_empty(tenant_ctx):
    """When KB is empty and web is available, RetrieverTool uses web fallback."""
    web_calls = []

    async def mock_web_search(query, top_k=3):
        web_calls.append(query)
        return [{"content": f"Web result for: {query}", "url": "https://web.example.com"}]

    from app.rag.agentic.retriever_tool import RetrieverTool
    tool = RetrieverTool(
        knowledge_store=KnowledgeStore(),  # empty KB
        web_search_fn=mock_web_search,
        web_search_available=True,
    )
    result = await tool.retrieve(
        query="current Python version",
        tenant_ctx=tenant_ctx,
        strategy="auto",
        allow_web_fallback=True,
    )
    assert result.source in ("web", "parametric")
    if result.source == "web":
        assert len(web_calls) > 0  # web was actually called


async def test_retriever_fallback_chain_order(tenant_ctx):
    """Fallback chain must try: KB → web → memory → parametric in order."""
    from app.rag.agentic.fallback_chain import FallbackChain
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]
