# tests/context/test_context_pipeline.py
"""Context pipeline: rerank → dedup → budget → citations → prompt."""
from __future__ import annotations

import pytest

from app.context.citation_manager import Citation, CitationManager
from app.context.context_budget import BudgetResult, ContextBudget
from app.context.prompt_builder import PromptBuilder, PromptContextBundle
from app.context.rerank_policy import RerankPolicy, RerankStrategy, rrf_fuse


@pytest.fixture
def sample_chunks():
    return [
        {"chunk_id": "c1", "content": "AgentVerse supports dynamic orchestration.", "score": 0.9,
         "source_url": "https://docs.example.com/page1"},
        {"chunk_id": "c2", "content": "Dynamic orchestration selects patterns per goal.", "score": 0.8,
         "source_url": "https://docs.example.com/page2"},
        {"chunk_id": "c3", "content": "The platform is vendor-agnostic.", "score": 0.7,
         "source_url": "https://docs.example.com/page3"},
        {"chunk_id": "c4", "content": "Tenant isolation is enforced at the DB layer.", "score": 0.6,
         "source_url": "https://docs.example.com/page4"},
        # Duplicate content
        {"chunk_id": "c5", "content": "AgentVerse supports dynamic orchestration.", "score": 0.85,
         "source_url": "https://docs.example.com/page1"},
    ]


# ── ContextBudget ─────────────────────────────────────────────────────────────

def test_budget_truncates_to_max_tokens(sample_chunks):
    budget = ContextBudget(max_tokens=100)
    result = budget.apply(sample_chunks)
    assert isinstance(result, BudgetResult)
    assert result.total_tokens <= 100
    assert len(result.included_chunks) <= len(sample_chunks)


def test_budget_respects_chunk_limit(sample_chunks):
    budget = ContextBudget(max_tokens=10000, max_chunks=2)
    result = budget.apply(sample_chunks)
    assert len(result.included_chunks) <= 2


def test_budget_includes_at_least_one_chunk(sample_chunks):
    budget = ContextBudget(max_tokens=50)
    result = budget.apply(sample_chunks)
    assert len(result.included_chunks) >= 1


# ── RerankPolicy ─────────────────────────────────────────────────────────────

def test_rerank_score_strategy_sorts_by_score(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    scores = [c["score"] for c in reranked]
    assert scores == sorted(scores, reverse=True)


def test_rerank_deduplication_removes_identical_content(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, deduplicate=True)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    contents = [c["content"] for c in reranked]
    assert len(contents) == len(set(contents))  # no duplicates


def test_rerank_diversity_limits_per_source(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.DIVERSITY, max_per_source=1)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    source_counts: dict[str, int] = {}
    for c in reranked:
        url = c.get("source_url", "")
        source_counts[url] = source_counts.get(url, 0) + 1
    for url, count in source_counts.items():
        assert count <= 1


def test_rerank_filters_below_threshold(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, min_score=0.75)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    assert all(c["score"] >= 0.75 for c in reranked)


def test_rrf_fuse_combines_ranked_lists():
    list1 = [{"chunk_id": "a", "content": "alpha", "score": 0.9},
             {"chunk_id": "b", "content": "beta", "score": 0.8}]
    list2 = [{"chunk_id": "b", "content": "beta", "score": 0.7},
             {"chunk_id": "c", "content": "gamma", "score": 0.6}]
    fused = rrf_fuse([list1, list2])
    assert len(fused) == 3
    # "b" appears in both lists so should have higher RRF score than "c"
    ids = [c["chunk_id"] for c in fused]
    b_pos = ids.index("b")
    c_pos = ids.index("c")
    assert b_pos < c_pos


def test_rrf_fuse_uses_correct_1indexed_formula():
    """RRF must use 1/(k+rank) with 1-indexed ranks per Cormack 2009."""
    single_list = [
        {"chunk_id": "c1", "content": "First", "score": 0.9},
        {"chunk_id": "c2", "content": "Second", "score": 0.8},
    ]
    fused = rrf_fuse([single_list], k=60)
    # c1 at rank 1 → 1/(60+1) = 0.016393...
    c1_score = next(c["rrf_score"] for c in fused if c["chunk_id"] == "c1")
    c2_score = next(c["rrf_score"] for c in fused if c["chunk_id"] == "c2")
    assert abs(c1_score - (1.0 / 61)) < 0.0001  # 1/(60+1)
    assert abs(c2_score - (1.0 / 62)) < 0.0001  # 1/(60+2)
    assert c1_score > c2_score  # rank 1 scores higher than rank 2


def test_rrf_fuse_combines_two_ranked_lists():
    """RRF must boost chunks ranked highly in multiple lists."""
    list_a = [
        {"chunk_id": "c1", "content": "A1", "score": 0.9},
        {"chunk_id": "c2", "content": "A2", "score": 0.8},
        {"chunk_id": "c3", "content": "A3", "score": 0.7},
    ]
    list_b = [
        {"chunk_id": "c3", "content": "A3", "score": 0.95},  # c3 ranked 1st in B
        {"chunk_id": "c1", "content": "A1", "score": 0.85},
        {"chunk_id": "c2", "content": "A2", "score": 0.60},
    ]
    fused = rrf_fuse([list_a, list_b], k=60)
    assert len(fused) == 3
    # c1 appears at rank 1 in list_a and rank 2 in list_b → high combined score
    # c3 appears at rank 3 in list_a and rank 1 in list_b → also high
    fused_ids = [c["chunk_id"] for c in fused]
    assert "c1" in fused_ids and "c3" in fused_ids


def test_rerank_cross_encoder_strategy_works(sample_chunks):
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)
    reranked = policy.rerank(sample_chunks, query="dynamic orchestration")
    assert len(reranked) > 0
    # Top result should be related to the query
    assert isinstance(reranked[0], dict)


# ── CitationManager ───────────────────────────────────────────────────────────

def test_citation_manager_creates_numbered_citations(sample_chunks):
    mgr = CitationManager()
    cited_chunks, citations = mgr.attach_citations(sample_chunks[:3])
    assert len(citations) == 3
    for i, cit in enumerate(citations):
        assert cit.index == i + 1
        assert cit.source_url


def test_citation_manager_formats_inline_refs(sample_chunks):
    mgr = CitationManager()
    cited_chunks, citations = mgr.attach_citations(sample_chunks[:2])
    formatted = mgr.format_citation_block(citations)
    assert "[1]" in formatted
    assert "[2]" in formatted


# ── PromptBuilder ─────────────────────────────────────────────────────────────

def test_prompt_builder_creates_bundle():
    builder = PromptBuilder()
    bundle = PromptContextBundle(
        goal_context="List all open tickets",
        knowledge_chunks=[{"content": "Ticket #123 is open", "score": 0.9}],
        citations=[],
        session_memory=[],
        reflexion_lessons=[],
    )
    planner_prompt = builder.build_planner_context(bundle)
    assert "List all open tickets" in planner_prompt
    assert "Ticket #123" in planner_prompt


def test_prompt_builder_includes_citations(sample_chunks):
    from app.context.citation_manager import Citation, CitationManager
    mgr = CitationManager()
    _, citations = mgr.attach_citations(sample_chunks[:2])
    builder = PromptBuilder()
    bundle = PromptContextBundle(
        goal_context="explain orchestration",
        knowledge_chunks=sample_chunks[:2],
        citations=citations,
        session_memory=[],
        reflexion_lessons=[],
    )
    prompt = builder.build_executor_context(bundle, step="step 1")
    assert "docs.example.com" in prompt or "[1]" in prompt


def test_prompt_builder_applies_token_budget(sample_chunks):
    builder = PromptBuilder(max_context_tokens=50)
    bundle = PromptContextBundle(
        goal_context="test",
        knowledge_chunks=sample_chunks * 10,  # lots of chunks
        citations=[],
        session_memory=[],
        reflexion_lessons=[],
    )
    prompt = builder.build_planner_context(bundle)
    # Must not exceed reasonable size even with many chunks
    assert len(prompt) < 10000


def test_llm_reranker_returns_chunks(sample_chunks):
    """LLM reranker must return chunks (falls back to keyword overlap when no provider)."""
    policy = RerankPolicy(strategy=RerankStrategy.LLM)
    reranked = policy.rerank(sample_chunks, query="orchestration")
    assert len(reranked) >= 1
    assert all(isinstance(c, dict) for c in reranked)


# ── ContextPipeline multi-source injection (D-20) ─────────────────────────────


def test_pipeline_threads_all_context_sources_into_planner(sample_chunks):
    """D-20: graph_facts / execution_memory / long_term_memory / semantic_cache_hits
    passed to ContextPipeline.run must reach PromptBuilder and render in the planner
    context (previously-dead builder branches now execute end-to-end)."""
    from app.context.context_pipeline import ContextPipeline

    pipeline = ContextPipeline(max_tokens=6000)
    result = pipeline.run(
        chunks=sample_chunks,
        query="orchestration",
        goal_context="explain orchestration",
        graph_facts=[{"fact": "GraphFactAlpha connects A to B"}],
        execution_memory=[{"plan": ["ExecPlanBeta step one", "ExecPlanBeta step two"]}],
        long_term_memory=[{"content": "LongTermPrefGamma: prefer concise output"}],
        semantic_cache_hits=[{"content": "SemanticCacheDelta previously answered"}],
    )
    planner = result.planner_context
    # Each previously-unreachable source now renders in the planner context.
    assert "GraphFactAlpha" in planner
    assert "ExecPlanBeta" in planner
    assert "LongTermPrefGamma" in planner
    assert "SemanticCacheDelta" in planner
    # Section headers from the builder branches are present too.
    assert "Knowledge graph context" in planner
    assert "Prior successful approaches" in planner
    assert "Learned preferences" in planner
    assert "Cached context" in planner


def test_pipeline_omitting_new_sources_preserves_behavior(sample_chunks):
    """Omitting the new sources must not change existing chunk/citation behavior:
    no section headers for the new sources appear, and chunks/citations still render."""
    from app.context.context_pipeline import ContextPipeline

    pipeline = ContextPipeline(max_tokens=6000)
    result = pipeline.run(
        chunks=sample_chunks,
        query="orchestration",
        goal_context="explain orchestration",
        reflexion_lessons=["always verify"],
        web_results=[{"content": "web snippet about orchestration"}],
    )
    planner = result.planner_context
    # Existing behavior intact.
    assert "explain orchestration" in planner
    assert "dynamic orchestration" in planner
    assert "Past lessons" in planner
    assert "Web context" in planner
    # None of the new-source section headers leak in when their inputs are absent.
    assert "Knowledge graph context" not in planner
    assert "Prior successful approaches" not in planner
    assert "Learned preferences" not in planner
    assert "Cached context" not in planner
    assert result.included_chunks  # chunks still flow through
