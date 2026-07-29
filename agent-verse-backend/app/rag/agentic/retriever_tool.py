"""Agent retrieval tool backed exclusively by the tenant-aware gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.rag.contracts import RAGStrategy

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class CitationRef:
    source: str
    url: str = ""
    page_number: int | None = None
    chunk_id: str = ""
    score: float = 0.0
    citation_id: str = ""
    collection_id: str = ""


@dataclass
class RetrievalResult:
    """Structured gateway result used by agent retrieval callers."""

    query: str
    source: str
    strategy_used: str
    confidence: float
    chunks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    reformulation_count: int = 0
    context_text: str = ""
    corrected: bool = False
    correction_reason: str = ""
    requested_strategy_id: str = ""
    resolved_strategy_ids: list[str] = field(default_factory=list)
    retrieval_legs: list[dict[str, Any]] = field(default_factory=list)
    strategy_trace: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.context_text:
            self.context_text = "\n\n".join(
                str(chunk.get("content", "")) for chunk in self.chunks
            )


class RetrieverTool:
    """Thin adapter from agent callers to canonical gateway executions."""

    def __init__(self, *, retrieval_gateway: Any) -> None:
        if retrieval_gateway is None:
            raise ValueError("retrieval_gateway is required")
        self._gateway = retrieval_gateway

    async def retrieve(
        self,
        query: str,
        *,
        tenant_ctx: TenantContext,
        strategy: RAGStrategy = RAGStrategy.HYBRID,
        collection_ids: list[str] | None = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
        execution_id: str = "",
        **legacy_options: Any,
    ) -> RetrievalResult:
        """Retrieve the requested canonical strategy without fallback or promotion."""

        if legacy_options:
            raise TypeError("Fallback and reformulation options are not supported")
        if not isinstance(strategy, RAGStrategy):
            raise TypeError("strategy must be a canonical RAGStrategy")
        if not collection_ids:
            raise ValueError("collection_ids is required")

        gateway_results = []
        for collection_id in collection_ids:
            execute_kwargs: dict[str, Any] = {
                "collection_id": collection_id,
                "query": query,
                "strategy_id": strategy,
                "top_k": top_k,
                "filters": metadata_filter or {},
            }
            if execution_id:
                execute_kwargs["execution_id"] = execution_id
            gateway_results.append(await self._gateway.execute(tenant_ctx, **execute_kwargs))

        citations_by_id = {
            (collection_id, citation.source, citation.citation_id): (
                collection_id,
                citation,
            )
            for collection_id, result in zip(collection_ids, gateway_results, strict=True)
            for citation in result.citations
            if citation.score >= min_confidence
        }
        ordered = sorted(
            citations_by_id.values(),
            key=lambda item: (-item[1].score, item[1].citation_id),
        )[:top_k]
        chunks = [
            {
                "citation_id": citation.citation_id,
                "chunk_id": citation.chunk_id,
                "content": citation.content,
                "score": citation.score,
                "source": citation.source,
                "collection_id": collection_id,
                "metadata": dict(citation.metadata),
            }
            for collection_id, citation in ordered
        ]
        citations = [
            CitationRef(
                source=citation.source,
                url=str(citation.metadata.get("source_url", "")),
                page_number=citation.metadata.get("page_number"),
                chunk_id=citation.chunk_id,
                score=citation.score,
                citation_id=citation.citation_id,
                collection_id=collection_id,
            )
            for collection_id, citation in ordered
        ]
        resolved_ids = sorted(
            {result.resolved_strategy_id.value for result in gateway_results}
        )
        return RetrievalResult(
            query=query,
            source="knowledge_base",
            strategy_used=resolved_ids[0] if len(resolved_ids) == 1 else strategy.value,
            confidence=max((citation.score for _, citation in ordered), default=0.0),
            chunks=chunks,
            citations=citations,
            requested_strategy_id=strategy.value,
            resolved_strategy_ids=resolved_ids,
            retrieval_legs=[
                leg.model_dump(mode="json")
                for result in gateway_results
                for leg in result.retrieval_legs
            ],
            strategy_trace=[
                trace.model_dump(mode="json")
                for result in gateway_results
                for trace in result.strategy_trace
            ],
        )

    async def parallel_retrieve(
        self,
        query: str,
        *,
        tenant_ctx: TenantContext,
        strategies: list[RAGStrategy],
        collection_ids: list[str],
        top_k: int = 5,
        min_confidence: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
        execution_id: str = "",
    ) -> list[RetrievalResult]:
        """Execute only the explicitly requested canonical strategies."""

        return list(
            await asyncio.gather(
                *(
                    self.retrieve(
                        query,
                        tenant_ctx=tenant_ctx,
                        strategy=strategy,
                        collection_ids=collection_ids,
                        top_k=top_k,
                        min_confidence=min_confidence,
                        metadata_filter=metadata_filter,
                        execution_id=execution_id,
                    )
                    for strategy in strategies
                )
            )
        )

    async def retrieve_corrective(
        self,
        query: str,
        *,
        tenant_ctx: TenantContext,
        collection_ids: list[str],
        top_k: int = 5,
        confidence_threshold: float = 0.0,
        strategy: RAGStrategy = RAGStrategy.CORRECTIVE,
        execution_id: str = "",
        **legacy_options: Any,
    ) -> RetrievalResult:
        """Execute canonical corrective retrieval without local correction fallback."""

        if legacy_options:
            raise TypeError("Fallback options are not supported")
        return await self.retrieve(
            query,
            tenant_ctx=tenant_ctx,
            strategy=strategy,
            collection_ids=collection_ids,
            top_k=top_k,
            min_confidence=confidence_threshold,
            execution_id=execution_id,
        )
