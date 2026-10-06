"""Shared worker-side ingestion services for every Celery ingestion task.

The FastAPI lifespan wires the knowledge store, the query embedder and the
RAG_INGEST guardrail rule repository for the API; it never runs in a Celery
worker. Each ingestion task used to rebuild those pieces itself, and the
repository-ingest task forgot the guardrail binding (RV-06): in a fresh worker
process its screens refused to run in production and ignored the tenant's own
block rules elsewhere. Every worker ingestion builder goes through
:func:`build_worker_knowledge_services`, so none can miss a piece.
"""

from __future__ import annotations

from typing import Any


def bind_worker_guardrail_rules(db_factory: object) -> None:
    """Bind the guardrail engine to the tenant's persisted rules (RV-06).

    ``app.main``'s lifespan does this for the API; it never runs in a worker, so
    ``ensure_tenant_loaded`` was a no-op here and worker-ingested documents were
    screened against the baseline defaults only — a tenant's own block rules
    never applied. One implementation for every worker path (ingestion, goals,
    workflows): :func:`app.guardrails_v2.worker_binding.bind_worker_guardrail_rules`.
    A failure here propagates: ingestion must not run unscreened (and
    ``screen_text`` refuses to screen in production without a repository).
    """
    from app.guardrails_v2.worker_binding import (
        bind_worker_guardrail_rules as _bind,
    )

    _bind(db_factory)


def build_worker_knowledge_services(db_factory: Any) -> tuple[Any, Any]:
    """(DB-backed KnowledgeStore, query embedder) for a worker ingestion task.

    Also binds the guardrail rule repository and the shared embedding-usage
    counters, so whatever the task ingests is screened against the tenant's
    rules and its embeds count toward the tenant's usage.
    """
    from app.embedding.usage import configure_usage_redis_from_env
    from app.providers.embedder_factory import embedder_model_name, resolve_embedder
    from app.rag.semantic_cache import bump_knowledge_generation
    from app.rag.store import KnowledgeStore

    # The SAME embedder the API's retrieval embeds queries with (not the chat
    # provider); resolve_embedder applies the NVIDIA / on-prem endpoint itself.
    # wire_registry_store: a saved embedding preference order picks the same
    # model here as in the API.
    resolution = resolve_embedder(wire_registry_store=True)
    store = KnowledgeStore(
        db_factory,
        embedding_dim=resolution.dimension,
        # Collections this worker writes first record the real model (USR-3).
        embedder_name=embedder_model_name(resolution.embedder) or None,
    )
    # Indexed documents invalidate answers the API replicas cached from the
    # tenant's old knowledge (shared Redis generation).
    store.add_change_listener(bump_knowledge_generation)
    configure_usage_redis_from_env()
    bind_worker_guardrail_rules(db_factory)
    return store, resolution.embedder


__all__ = ["bind_worker_guardrail_rules", "build_worker_knowledge_services"]
