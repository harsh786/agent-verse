"""RAG Platform API - unified retrieval with multiple strategies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.orchestration.strategy_registry import get_strategy_registry
from app.rag.contracts import (
    RAGStrategy,
    UnavailableRAGStrategyError,
    UnknownRAGStrategyError,
    resolve_rag_strategy,
)

router = APIRouter(prefix="/rag", tags=["rag-platform"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    collection_id: str | None = None
    strategy: str = RAGStrategy.ADAPTIVE.value
    top_k: int = Field(default=5, ge=1, le=20)


def _resolve_request_strategy(strategy_id: str) -> RAGStrategy:
    """Resolve an API strategy ID and translate contract errors to stable HTTP responses."""

    try:
        strategy = resolve_rag_strategy(strategy_id)
    except UnknownRAGStrategyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not get_strategy_registry().is_available(strategy.value):
        unavailable_error = UnavailableRAGStrategyError(strategy)
        raise HTTPException(status_code=503, detail=str(unavailable_error)) from unavailable_error
    return strategy


@router.post("/query")
async def rag_query(request: Request, body: RAGQueryRequest) -> dict[str, Any]:
    """Execute a RAG query with the specified strategy."""
    tenant = _require_tenant(request)
    from app.rag_platform.retriever import rag_retriever

    provider = getattr(request.app.state, "_app_provider", None)
    knowledge_store = getattr(request.app.state, "knowledge_store", None)

    try:
        from app.knowledge_graph.store import kg_store

        rag_retriever.set_dependencies(
            provider=provider, knowledge_store=knowledge_store, kg_store=kg_store
        )
    except ImportError:
        rag_retriever.set_dependencies(provider=provider, knowledge_store=knowledge_store)

    strategy = _resolve_request_strategy(body.strategy)

    result = await rag_retriever.retrieve(
        query=body.query,
        tenant_ctx=tenant,
        collection_id=body.collection_id,
        strategy=strategy,
        top_k=body.top_k,
    )

    return {
        "query": result.query,
        "strategy_used": result.strategy_used.value,
        "answer": result.answer,
        "citations": result.citations,
        "grounded": result.grounded,
        "confidence": round(result.confidence, 3),
        "retrieval_legs": [
            {
                "strategy": leg.strategy.value,
                "result_count": len(leg.results),
                "latency_ms": round(leg.latency_ms, 1),
            }
            for leg in result.legs
        ],
    }


@router.get("/strategies")
async def list_strategies(request: Request) -> dict[str, Any]:
    """List available RAG strategies."""
    _require_tenant(request)
    registry = get_strategy_registry()
    strategies: list[dict[str, Any]] = []
    for strategy in RAGStrategy:
        capability = registry.get(strategy.value)
        if capability is None:
            raise RuntimeError(f"Canonical RAG strategy is not registered: {strategy.value}")
        strategies.append(
            {
                "id": strategy.value,
                "name": strategy.value.replace("_", " ").title(),
                "description": capability.description,
                "state": capability.state.value,
                "available": registry.is_available(strategy.value),
            }
        )
    return {
        "strategies": strategies,
    }
