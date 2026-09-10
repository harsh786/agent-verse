"""Bounded capability-aware Adaptive RAG selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.code_rag import has_code_intent
from app.rag.contracts import RAGStrategy
from app.rag.engine import RetrievalPlanner, retrieve

_MEMORY_CONTEXT_TERMS = (
    "remember",
    "recall",
    "you mentioned",
    "we discussed",
    "we talked about",
    "earlier you",
    "last time",
    "previously discussed",
    "from our conversation",
    "my previous goal",
)


@dataclass(frozen=True, slots=True)
class AdaptiveDecision:
    strategy: RAGStrategy
    reason: str
    decision_count: int = 1


def select_adaptive_strategy(
    query: str,
    available_strategies: tuple[RAGStrategy, ...],
) -> AdaptiveDecision:
    """Make exactly one non-recursive decision from certified capabilities."""

    available = set(available_strategies) - {RAGStrategy.ADAPTIVE}
    if not available:
        raise ValueError("No certified retrieval strategy is currently available")
    normalized = query.lower()
    unavailable_reason = ""

    if any(term in normalized for term in _MEMORY_CONTEXT_TERMS):
        if RAGStrategy.MEMORY_AUGMENTED in available:
            return AdaptiveDecision(
                RAGStrategy.MEMORY_AUGMENTED,
                "memory_context_query; memory_augmented_available",
            )
        unavailable_reason += "memory_augmented_unavailable; "

    if has_code_intent(query):
        if RAGStrategy.CODE in available:
            return AdaptiveDecision(RAGStrategy.CODE, "code_query; code_available")
        unavailable_reason += "code_unavailable; "

    if any(term in normalized for term in ("graph", "relationship", "connected")):
        if RAGStrategy.GRAPH in available:
            return AdaptiveDecision(RAGStrategy.GRAPH, "graph_query; graph_available")
        unavailable_reason += "graph_unavailable; "

    if any(term in normalized for term in ("current", "latest", "web", "internet")):
        if RAGStrategy.WEB_AUGMENTED in available:
            return AdaptiveDecision(
                RAGStrategy.WEB_AUGMENTED,
                "freshness_query; web_augmented_available",
            )
        unavailable_reason += "web_augmented_unavailable; "

    # Self-RAG: the user explicitly asks whether a stated claim is correct, so a
    # retrieval-relevance self-critique loop is warranted over a single lookup.
    if any(
        term in normalized
        for term in (
            "fact-check",
            "fact check",
            "is this accurate",
            "is it accurate",
            "is this true",
            "is it true",
            "double-check",
            "double check",
        )
    ):
        if RAGStrategy.SELF_RAG in available:
            return AdaptiveDecision(
                RAGStrategy.SELF_RAG,
                "verification_claim_query; self_rag_available",
            )
        unavailable_reason += "self_rag_unavailable; "

    # Speculative: an explicit latency demand — draft-and-verify overlaps
    # generation with retrieval to return a grounded answer sooner.
    if any(
        term in normalized
        for term in (
            "quick",
            "quickly",
            "fast",
            "asap",
            "brief",
            "briefly",
            "tl;dr",
            "in short",
            "at a glance",
        )
    ):
        if RAGStrategy.SPECULATIVE in available:
            return AdaptiveDecision(
                RAGStrategy.SPECULATIVE,
                "latency_sensitive_query; speculative_available",
            )
        unavailable_reason += "speculative_unavailable; "

    # FLARE: long-form generation benefits from uncertainty-triggered retrieval
    # that fetches evidence mid-generation as the draft grows.
    if any(
        term in normalized
        for term in (
            "write ",
            "essay",
            "comprehensive",
            "in detail",
            "in-depth",
            "in depth",
            "detailed",
            "long-form",
            "long form",
            "draft ",
            "report on",
        )
    ):
        if RAGStrategy.FLARE in available:
            return AdaptiveDecision(
                RAGStrategy.FLARE,
                "long_form_generation_query; flare_available",
            )
        unavailable_reason += "flare_unavailable; "

    # Fusion: multi-facet / ambiguous queries benefit from query expansion with
    # reciprocal-rank fusion across the expanded variants.
    if any(
        term in normalized
        for term in (
            "pros and cons",
            "trade-offs",
            "tradeoffs",
            "options",
            "alternatives",
            "various",
            "several",
            "multiple",
            "different aspects",
            "facets",
            "ambiguous",
        )
    ):
        if RAGStrategy.FUSION in available:
            return AdaptiveDecision(
                RAGStrategy.FUSION,
                "multi_facet_query; fusion_available",
            )
        unavailable_reason += "fusion_unavailable; "

    # RAPTOR (D-6): a request for a broad, hierarchical roll-up over an entire
    # corpus is better served by tree-summary retrieval than a flat lookup. The
    # gateway dispatches RAPTOR through the precomputed-index core-strategy path
    # (DIRECT_CORE_RAG_STRATEGIES), so this is safe to auto-select once certified.
    if any(
        term in normalized
        for term in (
            "overview of",
            "high-level summary",
            "high level summary",
            "hierarchical summary",
            "bird's-eye view",
            "bird's eye view",
            "roll-up summary",
            "rollup summary",
            "summarize the entire",
            "condense the whole",
        )
    ):
        if RAGStrategy.RAPTOR in available:
            return AdaptiveDecision(
                RAGStrategy.RAPTOR,
                "hierarchical_summary_query; raptor_available",
            )
        unavailable_reason += "raptor_unavailable; "

    # Agentic RAG: an explicit multi-step research/investigation intent benefits
    # from an iterative retrieve-reason-retrieve loop over a single lookup. The
    # gateway has a dispatchable AgenticRAGRuntimeAdapter, so this becomes
    # auto-selectable (D-6) — but only when the capability is certified/available.
    if any(
        term in normalized
        for term in (
            "step by step",
            "step-by-step",
            "multi-step",
            "multi step",
            "investigate",
            "look into",
            "find out how",
        )
    ):
        if RAGStrategy.AGENTIC in available:
            return AdaptiveDecision(
                RAGStrategy.AGENTIC,
                "multi_step_research_query; agentic_available",
            )
        unavailable_reason += "agentic_unavailable; "

    # Agentic chunking (D-6): a comparative/tabular query benefits from
    # proposition-level retrieval over discrete structured facts rather than a
    # plain document-chunk lookup. Checked before the broader MULTI_HOP
    # "compare" signal so the more specific tabular shape wins.
    if any(
        term in normalized
        for term in (
            "tabular",
            "side-by-side",
            "side by side",
            "row-by-row",
            "row by row",
            "table format",
            "spreadsheet",
            "field-by-field",
            "field by field",
            "line-by-line",
            "line by line",
        )
    ):
        if RAGStrategy.AGENTIC_CHUNKING in available:
            return AdaptiveDecision(
                RAGStrategy.AGENTIC_CHUNKING,
                "tabular_comparison_query; agentic_chunking_available",
            )
        unavailable_reason += "agentic_chunking_unavailable; "

    # RAFT (D-6): an explicit request to answer from a trained/fine-tuned model
    # with grounding is what RAFT was built for. Checked before HYDE so the more
    # specific "explain ... grounding/fine-tuned" shape wins over HYDE's plain
    # "explain"/"what is" prefix match. Guarded: only selected when a completed,
    # compatible RAFT model is certified for the tenant (RAFT_MODEL readiness);
    # otherwise it falls through and the runtime adapter itself degrades to
    # hybrid retrieval rather than raising (D-8).
    if any(
        term in normalized
        for term in (
            "with grounding",
            "fine-tuned model",
            "fine tuned model",
            "trained model",
            "raft model",
            "grounded training",
        )
    ):
        if RAGStrategy.RAFT in available:
            return AdaptiveDecision(
                RAGStrategy.RAFT,
                "grounded_training_query; raft_available",
            )
        unavailable_reason += "raft_unavailable; "

    if any(term in normalized for term in ("compare", "contrast", "across")):
        if RAGStrategy.MULTI_HOP in available:
            return AdaptiveDecision(RAGStrategy.MULTI_HOP, "comparison_query; multi_hop_available")
        unavailable_reason += "multi_hop_unavailable; "
    if normalized.startswith(("what is", "explain", "describe")):
        if RAGStrategy.HYDE in available:
            return AdaptiveDecision(RAGStrategy.HYDE, "abstract_query; hyde_available")
        unavailable_reason += "hyde_unavailable; "
    if any(term in normalized for term in ("verify", "uncertain", "correct")):
        if RAGStrategy.CORRECTIVE in available:
            return AdaptiveDecision(
                RAGStrategy.CORRECTIVE,
                "verification_query; corrective_available",
            )
        unavailable_reason += "corrective_unavailable; "

    # ColBERT (D-6): a request for an exact/verbatim phrase or precise keyword
    # match benefits from token-level late-interaction scoring over plain
    # semantic similarity. Dependency-gated (RAGatouille + a locally cached
    # checkpoint, see `app/rag/readiness.py`) — `available_strategies` already
    # excludes COLBERT when that dependency probe fails, so this branch only
    # ever fires when the capability is truly ready; otherwise it falls through
    # to the next-best strategy rather than raising.
    if any(
        term in normalized
        for term in (
            "exact phrase",
            "exact wording",
            "exact term",
            "verbatim",
            "precise keyword match",
            "precise match",
        )
    ):
        if RAGStrategy.COLBERT in available:
            return AdaptiveDecision(
                RAGStrategy.COLBERT,
                "precision_keyword_query; colbert_available",
            )
        unavailable_reason += "colbert_unavailable; "

    # Modular RAG (D-6): a query that explicitly asks to run through a
    # structured, multi-module retrieval pipeline gets a richer, validated
    # pipeline instead of a single fixed strategy.
    if any(
        term in normalized
        for term in (
            "modular pipeline",
            "multi-module",
            "multi module",
            "pipeline stages",
            "chain the modules",
            "module graph",
            "through the pipeline",
        )
    ):
        if RAGStrategy.MODULAR in available:
            return AdaptiveDecision(
                RAGStrategy.MODULAR,
                "structured_pipeline_query; modular_available",
            )
        unavailable_reason += "modular_unavailable; "

    if RAGStrategy.HYBRID in available:
        return AdaptiveDecision(
            RAGStrategy.HYBRID,
            f"{unavailable_reason}selected_default_hybrid",
        )
    if RAGStrategy.NAIVE in available:
        return AdaptiveDecision(
            RAGStrategy.NAIVE,
            f"{unavailable_reason}hybrid_unavailable; selected_naive",
        )
    selected = sorted(available, key=lambda strategy: strategy.value)[0]
    return AdaptiveDecision(
        selected,
        f"{unavailable_reason}selected_only_available_capability",
    )


class AdaptiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "adaptive_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Adaptive RAG: uses RetrievalPlanner to auto-select strategy "
            "(lexical / multi_hop / hyde / fusion / direct) based on query heuristics."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    async def execute(
        self,
        *,
        session: Any,
        query: str,
        query_embedding: list[float] | None,
        collection_id: str,
        top_k: int = 10,
        provider: Any = None,
        embedding_dim: int | None = None,
        force_strategy: str | None = None,
        use_llm_transform: bool = False,
        **kwargs: Any,
    ) -> list[Any]:
        strategy = force_strategy or RetrievalPlanner().select_strategy(query)

        # For complex queries, optionally apply LLM query transformation and
        # merge results from all expanded queries.
        effective_queries = [query]
        if use_llm_transform and provider is not None:
            try:
                from app.rag.agentic.llm_query_transformer import LLMQueryTransformer

                transformer = LLMQueryTransformer(provider)
                effective_queries = await transformer.transform(query)
            except Exception:
                effective_queries = [query]

        all_results: list[Any] = []
        seen_ids: set[str] = set()
        for q in effective_queries:
            results = await retrieve(
                session,
                query=q,
                query_embedding=query_embedding,
                collection_id=collection_id,
                top_k=top_k,
                strategy=strategy,
                provider=provider,
                embedding_dim=embedding_dim,
            )
            for r in results:
                r_id = getattr(r, "chunk_id", None) or str(getattr(r, "content", r))[:80]
                if r_id not in seen_ids:
                    seen_ids.add(r_id)
                    all_results.append(r)
        return all_results[:top_k]
