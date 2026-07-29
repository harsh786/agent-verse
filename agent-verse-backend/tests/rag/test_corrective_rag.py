"""Corrective retrieval delegates to the canonical gateway strategy."""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.contracts import RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_corrective_retrieval_has_no_local_web_fallback() -> None:
    calls: list[dict[str, Any]] = []

    class Gateway:
        async def execute(
            self, tenant_ctx: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            calls.append(kwargs)
            return RAGExecutionResult(
                requested_strategy_id="corrective",
                resolved_strategy_id=RAGStrategy.CORRECTIVE,
            )

    result = await RetrieverTool(retrieval_gateway=Gateway()).retrieve_corrective(
        "policy",
        tenant_ctx=TenantContext("t1", PlanTier.PROFESSIONAL, "k1"),
        collection_ids=["collection-1"],
    )

    assert result.strategy_used == "corrective"
    assert calls[0]["strategy_id"] is RAGStrategy.CORRECTIVE
    assert not result.fallback_used
