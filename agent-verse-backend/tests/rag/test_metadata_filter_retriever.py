"""Metadata filters cross the RetrieverTool gateway boundary unchanged."""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.contracts import RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_retriever_tool_passes_metadata_filter() -> None:
    calls: list[dict[str, Any]] = []

    class Gateway:
        async def execute(
            self, tenant_ctx: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            calls.append(kwargs)
            return RAGExecutionResult(
                requested_strategy_id="hybrid",
                resolved_strategy_id=RAGStrategy.HYBRID,
            )

    await RetrieverTool(retrieval_gateway=Gateway()).retrieve(
        "policy",
        tenant_ctx=TenantContext("t1", PlanTier.PROFESSIONAL, "k1"),
        collection_ids=["collection-1"],
        metadata_filter={"department": "legal"},
    )

    assert calls[0]["filters"] == {"department": "legal"}
