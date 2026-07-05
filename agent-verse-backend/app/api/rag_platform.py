"""RAG Platform API - unified retrieval with multiple strategies."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from app.rag_platform.query_planner import RAGStrategy

router = APIRouter(prefix="/rag", tags=["rag-platform"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    collection_id: str | None = None
    strategy: str = "auto"
    top_k: int = Field(default=5, ge=1, le=20)


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

    try:
        strategy = RAGStrategy(body.strategy)
    except ValueError:
        strategy = RAGStrategy.AUTO

    result = await rag_retriever.retrieve(
        query=body.query,
        tenant_id=tenant.tenant_id,
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
    return {
        "strategies": [
            {"id": "auto", "name": "Auto", "description": "System selects best strategy"},
            {
                "id": "direct",
                "name": "Direct Vector",
                "description": "Simple embedding similarity search",
            },
            {
                "id": "multi_hop",
                "name": "Multi-Hop",
                "description": "Multi-turn retrieval for complex questions",
            },
            {
                "id": "hyde",
                "name": "HyDE",
                "description": "Hypothetical Document Embeddings for better recall",
            },
            {
                "id": "graph",
                "name": "GraphRAG",
                "description": "Knowledge graph-expanded retrieval",
            },
            {
                "id": "multimodal",
                "name": "Multimodal",
                "description": "Search across text, images, PDFs, and audio",
            },
        ]
    }
