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
from dataclasses import dataclass, field
from typing import Any

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.context.context_budget import ContextBudget
from app.context.citation_manager import CitationManager, Citation
from app.context.prompt_builder import PromptBuilder, PromptContextBundle


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

    def run(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        goal_context: str = "",
        step_context: str = "",
        session_memory: list[dict[str, Any]] | None = None,
        reflexion_lessons: list[str] | None = None,
        web_results: list[dict[str, Any]] | None = None,
    ) -> PipelineResult:
        original_count = len(chunks)
        reranked = self._reranker.rerank(chunks, query=query)
        dedup_removed = original_count - len(reranked)
        budget_result = self._budget.apply(reranked)
        included = budget_result.included_chunks
        cited_chunks, citations = self._citations_mgr.attach_citations(included)
        bundle = PromptContextBundle(
            goal_context=goal_context,
            knowledge_chunks=cited_chunks,
            citations=citations,
            session_memory=session_memory or [],
            reflexion_lessons=reflexion_lessons or [],
            web_results=web_results or [],
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
        )
