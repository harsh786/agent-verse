"""Embedding Platform API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.rls import sqlalchemy_rls_context

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
            None
            if matches
            else f"Dimension mismatch: model={config.dimension}, collection={collection_dim}"
        ),
    }


@router.get("/usage")
async def get_embedding_usage(request: Request) -> dict[str, Any]:
    """Get embedding usage statistics for the current session."""
    _require_tenant(request)
    from app.embedding.router import embedding_router

    return embedding_router.get_usage_stats()


async def _collection_avg_similarity(
    session: Any, collection_id: str, tenant_id: str
) -> float | None:
    """Average cosine similarity of a collection's chunk embeddings to their
    centroid (a semantic-drift signal: low similarity ⇒ the collection's vectors
    have spread out / drifted). Scoped to ``tenant_id`` so it never reads another
    tenant's vectors. Returns ``None`` when there are no embeddings or pgvector is
    unavailable, so callers degrade gracefully.
    """
    from sqlalchemy import text as _t

    try:
        row = (
            await session.execute(
                _t(
                    "WITH c AS ("
                    "  SELECT AVG(embedding) AS centroid FROM knowledge_chunks "
                    "  WHERE collection_id = :cid AND tenant_id = :tid "
                    "        AND embedding IS NOT NULL"
                    ") "
                    "SELECT AVG(1 - (k.embedding <=> c.centroid)) "
                    "FROM knowledge_chunks k, c "
                    "WHERE k.collection_id = :cid AND k.tenant_id = :tid "
                    "      AND k.embedding IS NOT NULL"
                ),
                {"cid": collection_id, "tid": tenant_id},
            )
        ).fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return float(row[0])


@router.get("/health/{collection_id}")
async def get_embedding_health(request: Request, collection_id: str) -> dict[str, Any]:
    """Per-collection embedding health check.

    Classifies semantic drift with :class:`EmbeddingDriftMonitor` and recommends
    re-embedding via :class:`ReembeddingPolicy` (the real policy components, not
    an ad-hoc heuristic), driven by a pgvector centroid-similarity drift signal.
    """
    import datetime as _dt

    from app.embedding.drift_monitor import DriftSeverity, EmbeddingDriftMonitor
    from app.embedding.reembedding_policy import ReembeddingPolicy, ReembeddingTrigger

    tenant = _require_tenant(request)
    tenant_id = str(getattr(tenant, "tenant_id", tenant))

    total_chunks = 0
    embedded_chunks = 0
    model = "openai/text-embedding-3-small"
    embedding_dim: int | None = None
    last_embedded_at: str | None = None
    avg_similarity: float | None = None

    try:
        from sqlalchemy import text as _t

        from app.db.session import get_session_factory

        db = get_session_factory()
        async with (
            db() as session,
            sqlalchemy_rls_context(session, tenant_id),
        ):
            # Every query is scoped to the caller's tenant — collection_id is a
            # client-supplied identifier, so an unscoped read would leak another
            # tenant's collection stats/model/drift (cross-tenant IDOR).
            row = (
                await session.execute(
                    _t(
                        "SELECT COUNT(*), COUNT(embedding), MAX(updated_at) "
                        "FROM knowledge_chunks "
                        "WHERE collection_id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).fetchone()
            if row:
                total_chunks = int(row[0] or 0)
                embedded_chunks = int(row[1] or 0)
                last_embedded_at = row[2].isoformat() if row[2] else None
            crow = (
                await session.execute(
                    _t(
                        "SELECT embedder, embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).fetchone()
            if crow:
                model = str(crow[0] or model)
                embedding_dim = int(crow[1]) if crow[1] is not None else None
            if embedded_chunks > 0:
                avg_similarity = await _collection_avg_similarity(
                    session, collection_id, tenant_id
                )
    except Exception:
        pass  # DB unavailable — degrade to coverage-only signal below

    coverage_pct = (embedded_chunks / max(total_chunks, 1)) * 100.0

    # Drift severity + score from the real monitor. When no semantic signal is
    # available (no embeddings / DB down), fall back to the embedding *coverage*
    # as the similarity proxy so an unembedded collection reads as drifted.
    monitor = EmbeddingDriftMonitor()
    similarity_signal = avg_similarity if avg_similarity is not None else (coverage_pct / 100.0)
    drift_severity: DriftSeverity = monitor.measure(similarity_signal, sample_size=embedded_chunks)
    drift_score = monitor.drift_score(similarity_signal)

    age_days = 0
    if last_embedded_at:
        try:
            last_dt = _dt.datetime.fromisoformat(last_embedded_at)
            now = _dt.datetime.now(last_dt.tzinfo)
            age_days = max(0, (now - last_dt).days)
        except ValueError:
            age_days = 0

    # Re-embedding recommendation from the real policy (same model → decision is
    # driven by drift + staleness + size).
    trigger: ReembeddingTrigger = ReembeddingPolicy().should_reembed(
        current_model=model,
        new_model=model,
        collection_size=total_chunks,
        drift_score=drift_score,
        age_days=age_days,
        old_dim=embedding_dim,
        new_dim=embedding_dim,
    )
    # An incomplete embedding coverage is itself a reason to (re-)embed.
    needs_reembed = trigger != ReembeddingTrigger.NONE or coverage_pct < 95.0

    return {
        "collection_id": collection_id,
        "total_chunks": total_chunks,
        "embedded_chunks": embedded_chunks,
        "coverage_pct": round(coverage_pct, 2),
        "model": model,
        "embedding_dim": embedding_dim,
        "last_embedded_at": last_embedded_at,
        "avg_similarity": round(avg_similarity, 4) if avg_similarity is not None else None,
        "drift_score": round(drift_score, 4),
        "drift_severity": drift_severity.value,
        "reembed_trigger": trigger.value,
        "needs_reembed": needs_reembed,
    }
