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
from app.rag.gateway import CollectionNotFoundError
from app.rag_platform.retriever import RAGRetriever, RAGSynthesisError
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/rag", tags=["rag-platform"])


def _require_tenant(request: Request) -> TenantContext:
    ctx = getattr(request.state, "tenant", None)
    if not isinstance(ctx, TenantContext):
        raise HTTPException(401, "Unauthorized")
    return ctx


class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    collection_id: str = Field(..., min_length=1)
    strategy: str = RAGStrategy.HYBRID.value
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict[str, Any] = Field(default_factory=dict)


def _resolve_request_strategy(strategy_id: str) -> RAGStrategy:
    """Resolve an API strategy ID and translate contract errors to stable HTTP responses."""

    try:
        strategy = resolve_rag_strategy(strategy_id)
    except UnknownRAGStrategyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return strategy


def _raise_retrieval_http_error(exc: Exception) -> None:
    """Map gateway failures to stable responses without exposing internals."""

    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, UnknownRAGStrategyError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, CollectionNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge collection not found") from exc
    if isinstance(exc, UnavailableRAGStrategyError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, RAGSynthesisError):
        raise HTTPException(status_code=503, detail="Answer synthesis is unavailable") from exc
    raise HTTPException(status_code=503, detail="Retrieval service is unavailable") from exc


def _gateway_capability_available(gateway: Any, strategy: RAGStrategy) -> bool:
    dependencies = getattr(gateway, "dependencies", None)
    capability = getattr(dependencies, "strategy_capabilities", {}).get(strategy)
    if capability is None:
        return False
    required = (
        ("requires_embedder", "embedder"),
        ("requires_provider", "llm_resolver"),
        ("requires_graph", "graph_capability"),
        ("requires_search", "search_capability"),
    )
    return all(
        not getattr(capability, requirement, False)
        or getattr(dependencies, dependency, None) is not None
        for requirement, dependency in required
    )


@router.post("/query")
async def rag_query(request: Request, body: RAGQueryRequest) -> dict[str, Any]:
    """Execute a RAG query with the specified strategy."""
    tenant = _require_tenant(request)
    _resolve_request_strategy(body.strategy)
    gateway = getattr(request.app.state, "retrieval_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="Retrieval service is unavailable")
    retriever = RAGRetriever(gateway=gateway)
    try:
        result = await retriever.retrieve(
            query=body.query,
            tenant_ctx=tenant,
            collection_id=body.collection_id,
            strategy=body.strategy,
            top_k=body.top_k,
            filters=body.filters,
        )
    except Exception as exc:
        _raise_retrieval_http_error(exc)

    return {
        "query": body.query,
        "requested_strategy_id": result.requested_strategy_id,
        "resolved_strategy_id": result.resolved_strategy_id.value,
        "strategy_used": result.resolved_strategy_id.value,
        "answer": result.answer,
        "citations": [citation.model_dump(mode="json") for citation in result.citations],
        "grounded": result.grounded,
        "confidence": round(max((citation.score for citation in result.citations), default=0.0), 3),
        "retrieval_legs": [leg.model_dump(mode="json") for leg in result.retrieval_legs],
        "strategy_trace": [trace.model_dump(mode="json") for trace in result.strategy_trace],
    }


@router.get("/strategies")
async def list_strategies(request: Request) -> dict[str, Any]:
    """List available RAG strategies."""
    _require_tenant(request)
    registry = get_strategy_registry()
    gateway = getattr(request.app.state, "retrieval_gateway", None)
    strategies: list[dict[str, Any]] = []
    for strategy in RAGStrategy:
        capability = registry.get(strategy.value)
        if capability is None:
            raise RuntimeError(f"Canonical RAG strategy is not registered: {strategy.value}")
        registry_available = registry.is_available(strategy.value)
        capability_available = _gateway_capability_available(gateway, strategy)
        strategies.append(
            {
                "id": strategy.value,
                "name": strategy.value.replace("_", " ").title(),
                "description": capability.description,
                "state": capability.state.value,
                "registry_available": registry_available,
                "capability_available": capability_available,
                "available": registry_available and capability_available,
            }
        )
    return {
        "strategies": strategies,
    }
