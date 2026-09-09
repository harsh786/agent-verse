"""D-7: gateway RAPTOR / AGENTIC_CHUNKING legs use the real precomputed path.

When a precomputed index exists for the collection the gateway must drive the
pattern's ``retrieve_precomputed`` -> ``store.search_precomputed_index`` path
rather than a bare metadata-filtered hybrid search. When no precomputed index
exists it must fall back cleanly to a plain hybrid retrieval and RECORD that
fallback (never silently return an empty result).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.contracts import RAGExecutionRequest, RAGStrategy
from app.rag.gateway import (
    RetrievalExecutionContext,
    RetrievalRuntimeDependencies,
    execute_core_strategy,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-1", plan=PlanTier.PROFESSIONAL, api_key_id="key-1")


def _request(strategy: RAGStrategy) -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query="What is the retention period?",
        requested_strategy_id=strategy.value,
        collection_id="collection-1",
        top_k=5,
    )


def _context(strategy: RAGStrategy) -> RetrievalExecutionContext:
    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(SimpleNamespace())

    return RetrievalExecutionContext(
        tenant_context=TENANT,
        strategy=strategy,
        filters={},
        dependencies=RetrievalRuntimeDependencies(
            embedder=SimpleNamespace(),
            llm=None,
            graph_capability=None,
            search_capability=None,
            policy_services=(),
        ),
        _db_operation_runner=runner,
    )


@pytest.mark.asyncio
async def test_raptor_invokes_real_precomputed_path_when_index_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"retrieve_precomputed": 0, "search_precomputed": 0}
    original = RAPTORPattern.retrieve_precomputed

    async def spy_retrieve(self: RAPTORPattern, **kwargs: Any) -> Any:
        calls["retrieve_precomputed"] += 1
        return await original(self, **kwargs)

    async def fake_search_precomputed(self: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls["search_precomputed"] += 1
        assert kwargs["strategy"] is RAGStrategy.RAPTOR
        return [
            {
                "chunk_id": "leaf-1",
                "content": "Alpha leaf detail",
                "score": 0.9,
                "metadata": {"node_type": "leaf", "hierarchy_level": 0},
                "citation_chunk_id": "leaf-1",
                "citation_content": "Alpha leaf detail",
            },
            {
                "chunk_id": "summary-1",
                "content": "Alpha hierarchy summary",
                "score": 0.8,
                "metadata": {"node_type": "summary", "hierarchy_level": 1},
                "citation_chunk_id": "summary-1",
                "citation_content": "Alpha hierarchy summary",
            },
        ]

    async def fake_embed(context: Any, text: str, strategy: RAGStrategy) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setattr(RAPTORPattern, "retrieve_precomputed", spy_retrieve)
    monkeypatch.setattr(
        "app.rag.gateway._GatewaySessionStore.search_precomputed_index",
        fake_search_precomputed,
    )
    monkeypatch.setattr("app.rag.gateway._embed_text", fake_embed)

    result = await execute_core_strategy(
        RAGStrategy.RAPTOR,
        _request(RAGStrategy.RAPTOR),
        _context(RAGStrategy.RAPTOR),
    )

    assert calls == {"retrieve_precomputed": 1, "search_precomputed": 1}
    assert {citation.chunk_id for citation in result.citations} == {"leaf-1", "summary-1"}
    trace = result.strategy_trace[-1]
    assert trace.detail["precomputed"] is True
    assert trace.detail["source"] == "precomputed_index"


@pytest.mark.asyncio
async def test_agentic_chunking_raises_when_index_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-7: empty precomputed index now raises UnavailableRAGStrategyError."""
    from app.rag.contracts import UnavailableRAGStrategyError

    async def empty_search(self: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def fake_embed(context: Any, text: str, strategy: RAGStrategy) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setattr(
        "app.rag.gateway._GatewaySessionStore.search_precomputed_index",
        empty_search,
    )
    monkeypatch.setattr("app.rag.gateway._embed_text", fake_embed)

    with pytest.raises(UnavailableRAGStrategyError, match="agentic_chunking"):
        await execute_core_strategy(
            RAGStrategy.AGENTIC_CHUNKING,
            _request(RAGStrategy.AGENTIC_CHUNKING),
            _context(RAGStrategy.AGENTIC_CHUNKING),
        )
