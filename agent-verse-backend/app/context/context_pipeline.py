"""ContextPipeline — orchestrates the 7-step context processing pipeline.

Spec §3.3 pipeline order:
  1. deduplicate chunks
  2. rerank by selected strategy
  3. filter below relevance threshold
  4. enforce source diversity
  5. apply token budget
  6. thread citations
  7. build planner/executor/verifier-specific context
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.context.citation_manager import Citation, CitationManager
from app.context.context_budget import ContextBudget
from app.context.prompt_builder import PromptBuilder, PromptContextBundle
from app.context.rerank_policy import RerankPolicy, RerankStrategy


@dataclass
class PipelineResult:
    included_chunks: list[dict[str, Any]]
    planner_context: str
    executor_context: str
    verifier_context: str
    citations: list[Citation]
    total_tokens: int
    dedup_removed: int = 0
    filtered_removed: int = 0


class ContextPipeline:
    def __init__(
        self,
        max_tokens: int = 6000,
        min_relevance_score: float = 0.35,
        max_chunks: int = 20,
        max_per_source: int = 5,
        rerank_strategy: RerankStrategy = RerankStrategy.SCORE,
        citation_required: bool = True,
        deduplication_enabled: bool = True,
        # BK3 (D-20 follow-up): optional, best-effort producers for the two
        # PromptBuilder branches that graph/rerank alone can't fill in.
        # Duck-typed and injected — the pipeline never hard-imports a
        # concrete store, so it behaves identically when neither is given.
        #   graph_source:   object with get_facts(query, tenant_id, top_k)
        #   semantic_cache: object with get_hits(query, tenant_id, top_k)
        graph_source: Any | None = None,
        semantic_cache: Any | None = None,
    ) -> None:
        self._budget = ContextBudget(max_tokens=max_tokens, max_chunks=max_chunks)
        self._reranker = RerankPolicy(
            strategy=rerank_strategy,
            deduplicate=deduplication_enabled,
            min_score=min_relevance_score,
            max_per_source=max_per_source,
        )
        self._citations_mgr = CitationManager()
        self._prompt_builder = PromptBuilder(max_context_tokens=max_tokens)
        self._graph_source = graph_source
        self._semantic_cache_source = semantic_cache

    def run(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        goal_context: str = "",
        step_context: str = "",
        session_memory: list[dict[str, Any]] | None = None,
        reflexion_lessons: list[str] | None = None,
        web_results: list[dict[str, Any]] | None = None,
        graph_facts: list[dict[str, Any]] | None = None,
        execution_memory: list[dict[str, Any]] | None = None,
        long_term_memory: list[dict[str, Any]] | None = None,
        semantic_cache_hits: list[dict[str, Any]] | None = None,
        tenant_id: str | None = None,
    ) -> PipelineResult:
        original_count = len(chunks)
        reranked = self._reranker.rerank(chunks, query=query)
        dedup_removed = original_count - len(reranked)
        # Value-based packing: greedily fill the token budget by value-per-token
        # density (relevance x trust x recency x usefulness) instead of first-fit,
        # maximising total context value. predict_chunk_value defaults missing
        # signals gracefully, so this degrades to score-ordered packing.
        budget_result = self._budget.apply(reranked, strategy="value")
        included = budget_result.included_chunks

        # Step: thread citation indices onto chunks before citation extraction
        try:
            from app.rag.agentic.citation_threader import CitationThreader

            threader = CitationThreader()
            if included:
                included = threader.thread(included)
        except Exception:
            pass

        cited_chunks, citations = self._citations_mgr.attach_citations(included)

        # BK3 (D-20 follow-up): when the caller doesn't supply graph_facts /
        # semantic_cache_hits explicitly (i.e. leaves them None), ask the
        # injected producers for them, best-effort. An explicit argument
        # (including []) always wins — the producers only fill a genuine gap.
        if graph_facts is None:
            graph_facts = self._fetch_graph_facts(query, tenant_id)
        if semantic_cache_hits is None:
            semantic_cache_hits = self._fetch_semantic_cache_hits(query, tenant_id)

        # D-20: thread the previously-unpopulated context sources into the bundle so
        # PromptBuilder's graph_facts / execution_memory / long_term_memory /
        # semantic_cache_hits branches actually render at runtime. Callers that omit
        # them get the historical chunks/reflexion/web behavior unchanged.
        # execution_memory / long_term_memory are forwarded by
        # app/agent/nodes/planner_mixin.py `_node_plan` from records
        # app/agent/nodes/rag_mixin.py stashes on agent_state.context. graph_facts /
        # semantic_cache_hits are, as of BK3, forwarded the same way when present on
        # agent_state.context, and otherwise fall back to the best-effort producers
        # above (graph_source / semantic_cache) when the pipeline was constructed
        # with them.
        bundle = PromptContextBundle(
            goal_context=goal_context,
            knowledge_chunks=cited_chunks,
            citations=citations,
            session_memory=session_memory or [],
            reflexion_lessons=reflexion_lessons or [],
            web_results=web_results or [],
            graph_facts=graph_facts or [],
            execution_memory=execution_memory or [],
            long_term_memory=long_term_memory or [],
            semantic_cache_hits=semantic_cache_hits or [],
        )
        planner_ctx = self._prompt_builder.build_planner_context(bundle)
        executor_ctx = self._prompt_builder.build_executor_context(bundle, step=step_context)
        verifier_ctx = self._prompt_builder.build_verifier_context(bundle)
        return PipelineResult(
            included_chunks=included,
            planner_context=planner_ctx,
            executor_context=executor_ctx,
            verifier_context=verifier_ctx,
            citations=citations,
            total_tokens=budget_result.total_tokens,
            dedup_removed=dedup_removed,
            # P1-6: chunks dropped by the token-budget filter (was always 0).
            filtered_removed=budget_result.excluded_count,
        )

    def _fetch_graph_facts(self, query: str, tenant_id: str | None) -> list[dict[str, Any]]:
        """Best-effort: ask the injected knowledge-graph source for facts
        relevant to *query*, tenant-scoped, top-K. Mirrors the try/except
        style of the execution_memory / long_term_memory branches — an
        absent source, no data, or any failure all degrade to []."""
        if self._graph_source is None:
            return []
        try:
            facts = self._graph_source.get_facts(query, tenant_id=tenant_id, top_k=5)
            return list(facts) if facts else []
        except Exception:
            return []

    def _fetch_semantic_cache_hits(
        self, query: str, tenant_id: str | None
    ) -> list[dict[str, Any]]:
        """Best-effort: tenant-scoped lookup against the injected semantic
        cache. An absent source, a miss, or any failure all degrade to []."""
        if self._semantic_cache_source is None:
            return []
        try:
            hits = self._semantic_cache_source.get_hits(query, tenant_id=tenant_id, top_k=2)
            return list(hits) if hits else []
        except Exception:
            return []
