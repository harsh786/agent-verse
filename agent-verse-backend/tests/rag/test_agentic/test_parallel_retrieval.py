"""Explicit canonical parallel retrieval tests."""

from __future__ import annotations

import pytest

from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.contracts import RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext


class Gateway:
    async def execute(self, tenant_ctx: TenantContext, **kwargs: object) -> RAGExecutionResult:
        strategy = kwargs["strategy_id"]
        assert isinstance(strategy, RAGStrategy)
        return RAGExecutionResult(
            requested_strategy_id=strategy.value,
            resolved_strategy_id=strategy,
        )


@pytest.mark.asyncio
async def test_parallel_retrieve_executes_only_requested_strategies() -> None:
    tenant = TenantContext("t1", PlanTier.PROFESSIONAL, "k1")
    results = await RetrieverTool(retrieval_gateway=Gateway()).parallel_retrieve(
        "policy",
        tenant_ctx=tenant,
        strategies=[RAGStrategy.HYBRID, RAGStrategy.GRAPH],
        collection_ids=["collection-1"],
    )

    assert [result.strategy_used for result in results] == ["hybrid", "graph"]


@pytest.mark.asyncio
async def test_parallel_retrieve_empty_strategy_list_is_empty() -> None:
    tenant = TenantContext("t1", PlanTier.PROFESSIONAL, "k1")
    results = await RetrieverTool(retrieval_gateway=Gateway()).parallel_retrieve(
        "policy",
        tenant_ctx=tenant,
        strategies=[],
        collection_ids=["collection-1"],
    )
    assert results == []
