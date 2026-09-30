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
    # Omit both to use the configured embedder's model. A requested model is
    # forwarded; one the provider does not serve is a 422 (it used to be
    # ignored, returning the default model's vectors).
    provider: str = ""
    model: str = ""
    # Opt-in only. When true and the provider is missing/failing, deterministic
    # hashing vectors (no semantic meaning) are returned and flagged as such.
    # The default used to be True — callers silently got fake 384-dim vectors.
    fallback_lexical: bool = False


@router.post("/embed")
async def embed_texts(request: Request, body: EmbedRequest) -> dict[str, Any]:
    """Embed texts on the app's configured embedding provider.

    503 when no provider is configured or it fails, unless the caller opted into
    ``fallback_lexical``; ``used_fallback`` reports whether the fallback actually
    ran, and ``model`` is what produced the vectors.
    """
    tenant = _require_tenant(request)
    from app.embedding.router import (
        EmbeddingModelUnavailableError,
        EmbeddingUnavailableError,
        embedding_router,
    )

    provider = getattr(request.app.state, "embedder", None) or getattr(
        request.app.state, "_app_provider", None
    )
    try:
        result = await embedding_router.embed_texts_report(
            body.texts,
            provider=body.provider,
            model=body.model,
            fallback_lexical=body.fallback_lexical,
            provider_impl=provider,
            tenant_id=tenant.tenant_id,
        )
    except EmbeddingModelUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmbeddingUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Embedding provider is unavailable (set fallback_lexical=true to accept "
            "non-semantic lexical vectors)",
        ) from exc
    embeddings = result.embeddings
    return {
        "embeddings": embeddings,
        "model": result.model,
        "requested_model": (f"{body.provider}/{body.model}" if body.model else None),
        "dimension": len(embeddings[0]) if embeddings else 0,
        "count": len(embeddings),
        "used_fallback": result.used_fallback,
        "fallback_reason": (result.error or None) if result.used_fallback else None,
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
    """The calling tenant's embedding usage statistics (this replica)."""
    tenant = _require_tenant(request)
    from app.embedding.router import embedding_router

    return embedding_router.get_usage_stats(tenant_id=tenant.tenant_id)


async def _collection_avg_similarity(
    session: Any, collection_id: str, tenant_id: str, table: str
) -> float | None:
    """Average cosine similarity of a collection's chunk embeddings to their
    centroid (a semantic-drift signal: low similarity ⇒ the collection's vectors
    have spread out / drifted). Scoped to ``tenant_id`` so it never reads another
    tenant's vectors. Returns ``None`` when there are no embeddings or pgvector is
    unavailable, so callers degrade gracefully.

    ``table`` is the collection's dimension-specific chunk table, resolved by the
    caller via :func:`app.rag.store._chunk_table`. There is no bare
    ``knowledge_chunks`` table in this schema and never has been — querying one
    raised UndefinedTable into the caller's ``except Exception`` and made this
    signal permanently unavailable.
    """
    from sqlalchemy import text as _t

    try:
        row = (
            await session.execute(
                _t(
                    "WITH c AS ("
                    f"  SELECT AVG(embedding) AS centroid FROM {table} "
                    "  WHERE collection_id = :cid AND tenant_id = :tid"
                    ") "
                    "SELECT AVG(1 - (k.embedding <=> c.centroid)) "
                    f"FROM {table} k, c "
                    "WHERE k.collection_id = :cid AND k.tenant_id = :tid"
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
            #
            # The collection is read FIRST because its embedding_dim names the
            # chunk table to count. Chunks live in knowledge_chunks_<dim>; the
            # bare "knowledge_chunks" this used to query has never existed, so
            # every call raised UndefinedTable into the except below and this
            # endpoint reported 0 chunks / 0% coverage / needs_reembed for every
            # collection in the system.
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

            if embedding_dim is not None:
                from app.rag.store import _chunk_table

                table = _chunk_table(embedding_dim)
                row = (
                    await session.execute(
                        _t(
                            # embedding is NOT NULL on these tables, so the row
                            # count IS the embedded count; created_at is the
                            # timestamp they carry (there is no updated_at).
                            f"SELECT COUNT(*), MAX(created_at) FROM {table} "
                            "WHERE collection_id = :cid AND tenant_id = :tid"
                        ),
                        {"cid": collection_id, "tid": tenant_id},
                    )
                ).fetchone()
                if row:
                    total_chunks = int(row[0] or 0)
                    embedded_chunks = total_chunks
                    last_embedded_at = row[1].isoformat() if row[1] else None
                if embedded_chunks > 0:
                    avg_similarity = await _collection_avg_similarity(
                        session, collection_id, tenant_id, table
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
