"""Gateway contract tests for RetrieverTool."""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext("t1", PlanTier.PROFESSIONAL, "k1")


class Gateway:
    def __init__(self, *, score: float = 0.8, error: Exception | None = None) -> None:
        self.score = score
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return RAGExecutionResult(
            requested_strategy_id=str(kwargs["strategy_id"]),
            resolved_strategy_id=RAGStrategy.HYBRID,
            citations=[
                RAGCitation(
                    citation_id="citation-1",
                    chunk_id="chunk-1",
                    content="Tenant evidence",
                    score=self.score,
                    source="guide.pdf",
                    metadata={"page_number": 3},
                )
            ],
        )


@pytest.mark.asyncio
async def test_retrieval_returns_gateway_content_and_citations(
    tenant_ctx: TenantContext,
) -> None:
    gateway = Gateway()
    result = await RetrieverTool(retrieval_gateway=gateway).retrieve(
        "policy",
        tenant_ctx=tenant_ctx,
        strategy=RAGStrategy.HYBRID,
        collection_ids=["collection-1"],
    )

    assert result.context_text == "Tenant evidence"
    assert result.citations[0].citation_id == "citation-1"
    assert result.citations[0].page_number == 3
    assert not result.fallback_used


@pytest.mark.asyncio
async def test_retrieval_respects_min_confidence(tenant_ctx: TenantContext) -> None:
    result = await RetrieverTool(retrieval_gateway=Gateway(score=0.2)).retrieve(
        "policy",
        tenant_ctx=tenant_ctx,
        collection_ids=["collection-1"],
        min_confidence=0.5,
    )

    assert result.chunks == []
    assert result.confidence == 0.0
    assert not result.fallback_used


@pytest.mark.asyncio
async def test_retrieval_rejects_noncanonical_strategy(
    tenant_ctx: TenantContext,
) -> None:
    gateway = Gateway()
    with pytest.raises(TypeError, match="canonical"):
        await RetrieverTool(retrieval_gateway=gateway).retrieve(
            "policy",
            tenant_ctx=tenant_ctx,
            strategy="auto",  # type: ignore[arg-type]
            collection_ids=["collection-1"],
        )
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_retrieval_failure_propagates_without_parametric_fallback(
    tenant_ctx: TenantContext,
) -> None:
    with pytest.raises(RuntimeError, match="gateway failed"):
        await RetrieverTool(
            retrieval_gateway=Gateway(error=RuntimeError("gateway failed"))
        ).retrieve(
            "policy",
            tenant_ctx=tenant_ctx,
            collection_ids=["collection-1"],
        )


@pytest.mark.asyncio
async def test_retrieval_passes_filters_and_top_k(tenant_ctx: TenantContext) -> None:
    gateway = Gateway()
    await RetrieverTool(retrieval_gateway=gateway).retrieve(
        "policy",
        tenant_ctx=tenant_ctx,
        strategy=RAGStrategy.GRAPH,
        collection_ids=["collection-1"],
        top_k=7,
        metadata_filter={"team": "legal"},
    )

    assert gateway.calls[0]["strategy_id"] is RAGStrategy.GRAPH
    assert gateway.calls[0]["top_k"] == 7
    assert gateway.calls[0]["filters"] == {"team": "legal"}
