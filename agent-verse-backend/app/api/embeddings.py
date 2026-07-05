"""Embedding Platform API."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., max_length=100)
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    fallback_lexical: bool = True


@router.post("/embed")
async def embed_texts(request: Request, body: EmbedRequest) -> dict[str, Any]:
    """Embed texts using the configured provider."""
    _require_tenant(request)
    from app.embedding.router import embedding_router

    provider = getattr(request.app.state, "_app_provider", None)
    if provider:
        embedding_router.set_provider(provider)

    embeddings = await embedding_router.embed_texts(
        body.texts,
        provider=body.provider,
        model=body.model,
        fallback_lexical=body.fallback_lexical,
    )
    return {
        "embeddings": embeddings,
        "model": f"{body.provider}/{body.model}",
        "dimension": len(embeddings[0]) if embeddings else 0,
        "count": len(embeddings),
        "used_fallback": provider is None,
    }


@router.get("/providers")
async def list_embedding_providers(request: Request) -> dict[str, Any]:
    """List available embedding providers and models."""
    _require_tenant(request)
    from app.embedding.router import BUILTIN_EMBEDDING_CONFIGS
    return {
        "providers": [
            {
                "key": key,
                "provider": cfg.provider,
                "model": cfg.model,
                "dimension": cfg.dimension,
                "cost_per_1k": cfg.cost_per_1k,
            }
            for key, cfg in BUILTIN_EMBEDDING_CONFIGS.items()
        ]
    }


@router.post("/validate-dimension")
async def validate_dimension(request: Request) -> dict[str, Any]:
    """Check if a model's dimension matches a collection's configured dimension."""
    _require_tenant(request)
    body = await request.json()
    from app.embedding.router import embedding_router

    config = embedding_router.get_config(body.get("provider", ""), body.get("model", ""))
    if not config:
        raise HTTPException(404, "Embedding config not found")

    collection_dim = body.get("collection_dimension", 0)
    matches = embedding_router.validate_dimension(collection_dim, config.dimension)
    return {
        "matches": matches,
        "model_dimension": config.dimension,
        "collection_dimension": collection_dim,
        "warning": (
            None if matches
            else f"Dimension mismatch: model={config.dimension}, collection={collection_dim}"
        ),
    }


@router.get("/usage")
async def get_embedding_usage(request: Request) -> dict[str, Any]:
    """Get embedding usage statistics for the current session."""
    _require_tenant(request)
    from app.embedding.router import embedding_router
    return embedding_router.get_usage_stats()
