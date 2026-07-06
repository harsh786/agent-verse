"""Embedding Platform API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
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


@router.get("/health/{collection_id}")
async def get_embedding_health(request: Request, collection_id: str) -> dict[str, Any]:
    """Per-collection embedding health check.

    Returns coverage, drift score, and whether a re-embed is recommended.
    """
    _require_tenant(request)

    total_chunks = 0
    embedded_chunks = 0
    model = "openai/text-embedding-3-small"
    last_embedded_at: str | None = None

    try:
        from sqlalchemy import text as _t

        from app.db.session import get_session_factory

        db = get_session_factory()
        async with db() as session:
            row = (
                await session.execute(
                    _t(
                        "SELECT COUNT(*), COUNT(embedding), MAX(updated_at) "
                        "FROM knowledge_chunks WHERE collection_id = :cid"
                    ),
                    {"cid": collection_id},
                )
            ).fetchone()
            if row:
                total_chunks = int(row[0] or 0)
                embedded_chunks = int(row[1] or 0)
                last_embedded_at = row[2].isoformat() if row[2] else None
    except Exception:
        pass  # DB unavailable — return stub with zeros

    coverage_pct = (embedded_chunks / max(total_chunks, 1)) * 100.0
    drift_score = (total_chunks - embedded_chunks) / max(total_chunks, 1)
    needs_reembed = coverage_pct < 95.0 or drift_score > 0.2

    return {
        "collection_id": collection_id,
        "total_chunks": total_chunks,
        "embedded_chunks": embedded_chunks,
        "coverage_pct": round(coverage_pct, 2),
        "model": model,
        "last_embedded_at": last_embedded_at,
        "drift_score": round(drift_score, 4),
        "needs_reembed": needs_reembed,
    }
