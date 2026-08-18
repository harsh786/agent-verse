"""Tenant-scoped knowledge store with persisted PostgreSQL retrieval.

In production this is backed by PostgreSQL + pgvector (HNSW index) and pg_trgm.
This pure-Python implementation is used in tests and as a fallback.

Hybrid search formula:
  score = 0.7 * cosine_similarity(query_vec, chunk_vec)
         + 0.3 * trigram_overlap(query_text, chunk_text)

When ``db_session_factory`` is supplied, async ingestion and retrieval use the
dimension-specific ``knowledge_chunks_*`` tables as their source of truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid as _uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.rag.models import Chunk, KnowledgeCollection
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.rag.contracts import RAGStrategy
    from app.rag.indexing import RAGIndexRecord

_VECTOR_WEIGHT = 0.7
_TRIGRAM_WEIGHT = 0.3
SUPPORTED_EMBEDDING_DIMENSIONS = (768, 1024, 1536, 3072)

_log = get_logger(__name__)


def _chunk_table(dimension: int) -> str:
    if dimension not in SUPPORTED_EMBEDDING_DIMENSIONS:
        raise ValueError(f"Unsupported embedding dimension: {dimension}")
    return f"knowledge_chunks_{dimension}"


@dataclass
class HybridSearchResult:
    chunk_id: str
    content: str
    score: float
    vector_score: float
    trigram_score: float
    source_url: str = ""
    source_doc_id: str = ""
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class EmbeddingProviderUnavailableError(RuntimeError):
    """A vector ingestion request has no usable embedding provider."""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _trigram_score(query: str, text: str) -> float:
    """Simple character trigram overlap score in [0, 1]."""

    def trigrams(s: str) -> set[str]:
        s = s.lower()
        return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()

    q_tris = trigrams(query)
    t_tris = trigrams(text)
    if not q_tris:
        return 0.0
    overlap = len(q_tris & t_tris)
    return overlap / len(q_tris)


@dataclass
class _CollectionStore:
    collection: KnowledgeCollection
    chunks: list[Chunk] = field(default_factory=list)


class KnowledgeStore:
    """Knowledge store with an in-memory fallback and PostgreSQL source of truth.

    Each collection is namespaced by (tenant_id, collection_id).

    Synchronous mutation helpers are only available without a DB factory. All
    persisted mutations use explicit awaited transaction boundaries.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        # Key: (tenant_id, collection_id) → _CollectionStore
        self._data: dict[tuple[str, str], _CollectionStore] = {}
        self._index_records: dict[tuple[str, str], list[RAGIndexRecord]] = {}
        self._db = db_session_factory

    def create_collection(
        self, collection: KnowledgeCollection, *, tenant_ctx: TenantContext
    ) -> str:
        """Create a collection in the explicit in-memory development store."""
        if self._db is not None:
            raise RuntimeError("Use create_collection_async for a persisted KnowledgeStore")
        key = (tenant_ctx.tenant_id, collection.collection_id)
        self._data[key] = _CollectionStore(collection=collection)
        return collection.collection_id

    async def create_collection_async(
        self,
        collection: KnowledgeCollection,
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        """Persist a collection before making it visible to the caller."""
        from opentelemetry import trace as _trace
        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("rag.create_collection") as span:
            span.set_attribute("tenant_id", tenant_ctx.tenant_id)
            span.set_attribute("collection_id", collection.collection_id)
        if self._db is None:
            return self.create_collection(collection, tenant_ctx=tenant_ctx)
        # Persist to DB first (fail-closed). Only expose in-memory after success.
        await self._db_create_collection(collection, tenant_ctx.tenant_id)
        self._data[(tenant_ctx.tenant_id, collection.collection_id)] = _CollectionStore(
            collection=collection
        )
        return collection.collection_id

    async def _db_create_collection(
        self, collection: KnowledgeCollection, tenant_id: str
    ) -> None:
        if self._db is None:
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            created_id = (
                await session.execute(
                    text(
                        "INSERT INTO knowledge_collections "
                        "(id, tenant_id, name, description, embedder, embedding_dim) "
                        "SELECT :id, :tid, :name, :description, :embedder, 768 "
                        "FROM tenants WHERE id = :tid AND is_active IS TRUE "
                        "RETURNING id"
                    ),
                    {
                        "id": collection.collection_id,
                        "tid": tenant_id,
                        "name": collection.name,
                        "description": collection.description,
                        "embedder": collection.embedder,
                    },
                )
            ).scalar_one_or_none()
            if created_id is None:
                raise KeyError(f"Active tenant not found: {tenant_id}")

    def get_collection(
        self, collection_id: str, *, tenant_ctx: TenantContext
    ) -> KnowledgeCollection | None:
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        return store.collection if store is not None else None

    def list_collections(self, *, tenant_ctx: TenantContext) -> list[KnowledgeCollection]:
        return [
            v.collection
            for (tid, _), v in self._data.items()
            if tid == tenant_ctx.tenant_id
        ]

    async def get_collection_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> KnowledgeCollection | None:
        """Read one active collection in the authenticated tenant scope."""
        if self._db is None:
            return self.get_collection(collection_id, tenant_ctx=tenant_ctx)
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT collection.id, collection.name, collection.description, "
                        "collection.document_count, collection.embedder "
                        "FROM knowledge_collections AS collection "
                        "JOIN tenants AS tenant ON tenant.id = collection.tenant_id "
                        "WHERE collection.id = :id AND collection.tenant_id = :tid "
                        "AND collection.is_active IS TRUE AND tenant.is_active IS TRUE"
                    ),
                    {"id": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            return None
        return KnowledgeCollection(
            name=str(row[1]),
            description=str(row[2] or ""),
            collection_id=str(row[0]),
            document_count=int(row[3] or 0),
            embedder=str(row[4] or "voyage"),
        )

    async def list_collections_async(
        self,
        *,
        tenant_ctx: TenantContext,
    ) -> list[KnowledgeCollection]:
        """List active collections without cross-tenant startup hydration."""
        if self._db is None:
            return self.list_collections(tenant_ctx=tenant_ctx)
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT collection.id, collection.name, collection.description, "
                        "collection.document_count, collection.embedder "
                        "FROM knowledge_collections AS collection "
                        "JOIN tenants AS tenant ON tenant.id = collection.tenant_id "
                        "WHERE collection.tenant_id = :tid AND collection.is_active IS TRUE "
                        "AND tenant.is_active IS TRUE ORDER BY collection.created_at"
                    ),
                    {"tid": tenant_ctx.tenant_id},
                )
            ).fetchall()
        return [
            KnowledgeCollection(
                name=str(row[1]),
                description=str(row[2] or ""),
                collection_id=str(row[0]),
                document_count=int(row[3] or 0),
                embedder=str(row[4] or "voyage"),
            )
            for row in rows
        ]

    async def delete_collection_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> bool:
        """Delete one owned collection and its cascaded persisted resources."""
        key = (tenant_ctx.tenant_id, collection_id)
        if self._db is None:
            return self._data.pop(key, None) is not None
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            deleted = (
                await session.execute(
                    text(
                        "DELETE FROM knowledge_collections "
                        "WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
                    ),
                    {"id": collection_id, "tenant_id": tenant_ctx.tenant_id},
                )
            ).scalar_one_or_none()
        if deleted is None:
            return False
        self._data.pop(key, None)
        return True

    async def create_ingestion_job_async(
        self,
        *,
        collection_id: str,
        source_url: str,
        source_type: str,
        title: str,
        tenant_ctx: TenantContext,
    ) -> str:
        """Create a durable ingestion job in the existing document-status table."""
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        job_id = _uuid.uuid4().hex
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            created = (
                await session.execute(
                    text("""
                        INSERT INTO knowledge_documents
                            (id, tenant_id, collection_id, title, source_url, source_type,
                             content_hash, status, chunk_count, domain_metadata,
                             job_source_hash)
                        SELECT :id, :tenant_id, collection.id, :title, :source_url,
                               :source_type, :content_hash, 'queued', 0,
                               CAST(:metadata AS jsonb), :source_hash
                        FROM knowledge_collections AS collection
                        JOIN tenants AS tenant ON tenant.id = collection.tenant_id
                        WHERE collection.id = :collection_id
                          AND collection.tenant_id = :tenant_id
                          AND collection.is_active IS TRUE
                          AND tenant.is_active IS TRUE
                        RETURNING id
                    """),
                    {
                        "id": job_id,
                        "tenant_id": tenant_ctx.tenant_id,
                        "collection_id": collection_id,
                        "title": title,
                        "source_url": source_url,
                        "source_type": source_type,
                        "content_hash": hashlib.sha256(source_url.encode()).hexdigest(),
                        "source_hash": hashlib.sha256(source_url.encode()).hexdigest(),
                        "metadata": json.dumps({"record_type": "ingestion_job"}),
                    },
                )
            ).scalar_one_or_none()
            if created is None:
                raise KeyError(f"Collection {collection_id} not found")
        return job_id

    async def update_ingestion_job_async(
        self,
        job_id: str,
        *,
        status: str,
        chunk_count: int,
        error_message: str | None,
        tenant_ctx: TenantContext,
        lease_owner: str | None = None,
    ) -> None:
        """Persist a sanitized terminal or progress state for one ingestion job."""
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        if status not in {"queued", "running", "completed", "failed"}:
            raise ValueError(f"Unsupported ingestion job status: {status}")
        if status == "running":
            job = await self.get_ingestion_job_async(job_id, tenant_ctx=tenant_ctx)
            if job is None:
                raise KeyError(f"Ingestion job not found: {job_id}")
            claimed = await self.claim_ingestion_job_async(
                job_id,
                collection_id=str(job["collection_id"]),
                source_url=str(job["source_url"]),
                lease_owner=lease_owner or f"legacy-{job_id}",
                lease_seconds=900,
                tenant_ctx=tenant_ctx,
            )
            if not claimed:
                raise RuntimeError(f"Ingestion job could not be claimed: {job_id}")
            return
        if status == "failed":
            await self.fail_ingestion_job_async(
                job_id,
                lease_owner=lease_owner,
                error_message=error_message or "Repository ingestion failed",
                tenant_ctx=tenant_ctx,
            )
            return
        if status == "queued":
            return
        raise RuntimeError("Repository completion must use the atomic chunk transaction")

    async def claim_ingestion_job_async(
        self,
        job_id: str,
        *,
        collection_id: str,
        source_url: str,
        lease_owner: str,
        lease_seconds: int,
        tenant_ctx: TenantContext,
    ) -> bool:
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        source_hash = hashlib.sha256(source_url.encode()).hexdigest()
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                text("""
                    UPDATE knowledge_documents
                    SET status = 'running', lease_owner = :lease_owner,
                        heartbeat_at = now(),
                        lease_expires_at = now() + make_interval(secs => :lease_seconds),
                        indexed_at = now(), error_message = NULL
                    WHERE id = :id AND tenant_id = :tenant_id
                      AND collection_id = :collection_id
                      AND source_type = 'repository'
                      AND source_url = :source_url AND job_source_hash = :source_hash
                      AND status = 'queued'
                      AND domain_metadata->>'record_type' = 'ingestion_job'
                """),
                {
                    "id": job_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "collection_id": collection_id,
                    "source_url": source_url,
                    "source_hash": source_hash,
                    "lease_owner": lease_owner,
                    "lease_seconds": lease_seconds,
                },
            )
        return bool(result.rowcount == 1)

    async def heartbeat_ingestion_job_async(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        tenant_ctx: TenantContext,
    ) -> bool:
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                text("""
                    UPDATE knowledge_documents
                    SET heartbeat_at = now(),
                        lease_expires_at = now() + make_interval(secs => :lease_seconds)
                    WHERE id = :id AND tenant_id = :tenant_id
                      AND status = 'running' AND lease_owner = :lease_owner
                      AND lease_expires_at > now()
                      AND domain_metadata->>'record_type' = 'ingestion_job'
                """),
                {
                    "id": job_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "lease_owner": lease_owner,
                    "lease_seconds": lease_seconds,
                },
            )
        return bool(result.rowcount == 1)

    async def fail_ingestion_job_async(
        self,
        job_id: str,
        *,
        lease_owner: str | None,
        error_message: str,
        tenant_ctx: TenantContext,
    ) -> str | None:
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await session.execute(
                text("""
                    UPDATE knowledge_documents
                    SET status = 'failed', error_message = :error_message,
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE id = :id AND tenant_id = :tenant_id
                      AND domain_metadata->>'record_type' = 'ingestion_job'
                      AND (
                          status = 'queued'
                          OR (status = 'running' AND lease_owner = :lease_owner)
                      )
                """),
                {
                    "id": job_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "lease_owner": lease_owner,
                    "error_message": error_message,
                },
            )
            status = (
                await session.execute(
                    text(
                        "SELECT status FROM knowledge_documents "
                        "WHERE id = :id AND tenant_id = :tenant_id"
                    ),
                    {"id": job_id, "tenant_id": tenant_ctx.tenant_id},
                )
            ).scalar_one_or_none()
        return str(status) if status is not None else None

    async def get_ingestion_job_async(
        self,
        job_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> dict[str, Any] | None:
        """Read one durable ingestion job in the active tenant scope."""
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                        SELECT id, collection_id, status, chunk_count,
                               error_message, source_url
                        FROM knowledge_documents
                        WHERE id = :id AND tenant_id = :tenant_id
                          AND domain_metadata->>'record_type' = 'ingestion_job'
                    """),
                    {"id": job_id, "tenant_id": tenant_ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            return None
        return {
            "job_id": str(row[0]),
            "collection_id": str(row[1]),
            "status": str(row[2]),
            "chunk_count": int(row[3] or 0),
            "error_message": str(row[4]) if row[4] else None,
            "source_url": str(row[5] or ""),
        }

    async def reconcile_stale_ingestion_jobs_async(
        self,
        *,
        tenant_ctx: TenantContext,
        stale_after_seconds: int,
    ) -> int:
        """Mark tenant-scoped running repository jobs interrupted after restart."""
        if self._db is None:
            raise RuntimeError("Durable ingestion jobs require a database")
        if stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                text("""
                    UPDATE knowledge_documents
                    SET status = 'failed',
                        error_message = 'Repository ingestion interrupted',
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE tenant_id = :tenant_id
                      AND source_type = 'repository'
                      AND domain_metadata->>'record_type' = 'ingestion_job'
                      AND (
                          (status = 'queued' AND created_at
                              < now() - make_interval(secs => :stale_after_seconds))
                          OR
                          (status = 'running' AND lease_expires_at < now())
                      )
                """),
                {
                    "tenant_id": tenant_ctx.tenant_id,
                    "stale_after_seconds": stale_after_seconds,
                },
            )
        return result.rowcount or 0

    def ingest_chunk(
        self,
        chunk: Chunk,
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> None:
        """Ingest one chunk into the explicit in-memory development store."""
        if self._db is not None:
            raise RuntimeError("Use ingest_chunks_async for a persisted KnowledgeStore")
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is None:
            raise KeyError(
                f"Collection {collection_id} not found for tenant {tenant_ctx.tenant_id}"
            )
        store.chunks.append(chunk)
        store.collection.document_count = len({c.document_id for c in store.chunks})

    async def _db_ingest_chunk(
        self, chunk: Chunk, collection_id: str, tenant_id: str
    ) -> None:
        if self._db is None:
            return
        await self._persist_chunk(
            chunk_id=chunk.chunk_id,
            collection_id=collection_id,
            tenant_id=tenant_id,
            document_id=chunk.document_id,
            content=chunk.content,
            embedding=chunk.embedding,
            metadata=dict(chunk.metadata),
            chunk_index=chunk.chunk_index,
            parent_chunk_id=chunk.parent_chunk_id,
            chunk_level=chunk.chunk_level,
            window_start=chunk.window_start,
            window_end=chunk.window_end,
        )

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[HybridSearchResult]:
        """In-memory hybrid search (fast path, always available)."""
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is None:
            return []

        chunks_to_score = list(store.chunks)
        if metadata_filter:
            chunks_to_score = [
                c for c in chunks_to_score
                if all(c.metadata.get(k) == v for k, v in metadata_filter.items())
            ]

        scored: list[HybridSearchResult] = []
        for chunk in chunks_to_score:
            vec_score = _cosine_similarity(query_embedding, chunk.embedding)
            tri_score = _trigram_score(query, chunk.content)
            hybrid = _VECTOR_WEIGHT * vec_score + _TRIGRAM_WEIGHT * tri_score
            scored.append(
                HybridSearchResult(
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    score=hybrid,
                    vector_score=vec_score,
                    trigram_score=tri_score,
                )
            )

        scored.sort(key=lambda r: r.score, reverse=True)

        # BM25 fourth leg — Okapi BM25 reranking post-sort
        # Blend: 0.7 * (cosine+trigram score) + 0.3 * normalized_BM25
        try:
            from app.rag.bm25 import BM25Retriever

            _bm25 = BM25Retriever()
            _bm25.index([{"chunk_id": r.chunk_id, "content": r.content} for r in scored])
            _bm25_hits = {
                h.chunk_id: h.score
                for h in _bm25.search(query, top_k=len(scored))
            }
            if _bm25_hits:
                _max_bm25 = max(_bm25_hits.values()) or 1.0
                for r in scored:
                    _bm25_s = _bm25_hits.get(r.chunk_id, 0.0) / _max_bm25
                    r.score = 0.7 * r.score + 0.3 * _bm25_s
                scored.sort(key=lambda r: r.score, reverse=True)
        except Exception:
            pass

        return scored[:top_k]

    async def hybrid_search_db(
        self,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
        retrieval_mode: str = "hybrid",
    ) -> list[HybridSearchResult]:
        """Search persisted chunks via pgvector, FTS, and pg_trgm RRF fusion."""
        if self._db is None:
            return self.hybrid_search(
                query, query_embedding, collection_id, tenant_ctx, top_k,
                metadata_filter=metadata_filter,
            )

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.rag.engine import RetrievalResult
        from app.rag.engine import hybrid_search as _engine_search

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dimension_row = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
            if dimension_row is None:
                return []
            embedding_dim = int(dimension_row[0])
            _chunk_table(embedding_dim)
            engine_results: list[RetrievalResult] = await _engine_search(
                session=session,
                query=query,
                query_embedding=query_embedding or None,
                collection_id=collection_id,
                top_k=top_k,
                retrieval_mode=retrieval_mode,
                embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                strict=True,
            )

        return [
            HybridSearchResult(
                chunk_id=result.chunk_id,
                content=result.content,
                score=result.score,
                vector_score=0.0,
                trigram_score=0.0,
                source_url=str(result.source_metadata.get("source_url", "") or ""),
                source_doc_id=str(result.source_metadata.get("source_doc_id", "") or ""),
                page_number=result.source_metadata.get("page_number"),
                metadata=result.source_metadata,
            )
            for result in engine_results
        ]

    async def search(
        self,
        query: str,
        collection_id: str,
        top_k: int = 10,
        *,
        tenant_ctx: TenantContext,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return plain-dict results from the configured source of truth."""
        from opentelemetry import trace as _trace
        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("rag.search") as span:
            span.set_attribute("tenant_id", tenant_ctx.tenant_id)
            span.set_attribute("collection_id", collection_id)
            span.set_attribute("top_k", top_k)
        if self._db is not None:
            persisted = await self.hybrid_search_db(
                query,
                [],
                collection_id,
                tenant_ctx,
                top_k=top_k,
                metadata_filter=metadata_filter,
                retrieval_mode="lexical",
            )
            return [
                {
                    "chunk_id": result.chunk_id,
                    "content": result.content,
                    "score": result.score,
                    "metadata": result.metadata,
                }
                for result in persisted
            ]

        results: list[dict[str, Any]] = []
        for (stored_tenant_id, cid), store in self._data.items():
            if cid != collection_id:
                continue
            if stored_tenant_id != tenant_ctx.tenant_id:
                continue
            for chunk in store.chunks:
                if metadata_filter and not all(
                    chunk.metadata.get(key) == value
                    for key, value in metadata_filter.items()
                ):
                    continue
                tri = _trigram_score(query, chunk.content)
                results.append({
                    "chunk_id": chunk.chunk_id,
                    "content": chunk.content,
                    "score": tri,
                    "metadata": chunk.metadata,
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def delete_document(
        self,
        document_id: str,
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> int:
        """Delete all chunks for a document. Returns count deleted."""
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is None:
            return 0
        before = len(store.chunks)
        store.chunks = [
            c for c in store.chunks if c.document_id != document_id
        ]
        deleted = before - len(store.chunks)
        if deleted > 0:
            store.collection.document_count = len(
                {c.document_id for c in store.chunks}
            )
        return deleted

    async def delete_document_async(
        self,
        document_id: str,
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
        db: Any = None,
    ) -> int:
        """Delete a persisted document using its collection's vector dimension."""
        _db = db or self._db
        if _db is None:
            return self.delete_document(
                document_id,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
            )

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            _db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dimension_row = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
            if dimension_row is None:
                return 0
            table = _chunk_table(int(dimension_row[0]))
            result = await session.execute(
                text(
                    f"DELETE FROM {table} WHERE document_id = :did "
                    "AND collection_id = :cid AND tenant_id = :tid"
                ),
                {
                    "did": document_id,
                    "cid": collection_id,
                    "tid": tenant_ctx.tenant_id,
                },
            )
            deleted = result.rowcount or 0
            if deleted:
                await session.execute(
                    text(f"""
                        UPDATE knowledge_collections
                        SET chunk_count = (
                                SELECT count(*) FROM {table} WHERE collection_id = :cid
                            ),
                            document_count = (
                                SELECT count(DISTINCT document_id) FROM {table}
                                WHERE collection_id = :cid
                            ),
                            updated_at = now()
                        WHERE id = :cid AND tenant_id = :tid
                    """),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )

        memory_deleted = self.delete_document(
            document_id,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        return max(deleted, memory_deleted)

    async def ingest_document(
        self,
        *,
        collection_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        tenant_ctx: TenantContext,
        embedder: Any = None,
        source_url: str = "",
        source_type: str = "text",
        source_doc_id: str = "",
        page_number: int | None = None,
        freshness_ttl_hours: int = 168,
        parent_chunk_id: str | None = None,
        chunk_level: str = "leaf",
        window_start: int | None = None,
        window_end: int | None = None,
        window_id: str | None = None,
        hierarchy_level: int = 0,
        is_proposition: bool = False,
        strategy_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Embed and transactionally persist one canonical retrieval chunk."""
        chunk_id = _uuid.uuid4().hex
        embedding: list[float] = []
        if self._db is not None and embedder is None:
            raise EmbeddingProviderUnavailableError("Embedding provider is unavailable")
        if embedder is not None:
            from app.providers.base import embed_texts

            try:
                embeddings = await embed_texts([content], provider=embedder)
                embedding = embeddings[0]
            except Exception as exc:
                if self._db is not None:
                    raise EmbeddingProviderUnavailableError(
                        "Embedding provider is unavailable"
                    ) from exc
                _log.warning("ingest_document_embed_failed: %s", exc)
        if self._db is not None and not embedding:
            raise EmbeddingProviderUnavailableError("Embedding provider is unavailable")

        document_id = source_doc_id or chunk_id
        merged_metadata = dict(metadata or {})
        merged_metadata.update({
            "source_url": source_url,
            "source_type": source_type,
            "source_doc_id": document_id,
            "page_number": page_number,
            "freshness_ttl_hours": freshness_ttl_hours,
        })

        chunk = Chunk(
            document_id=document_id,
            content=content,
            embedding=embedding,
            chunk_index=0,
            chunk_id=chunk_id,
            metadata=merged_metadata,
            parent_chunk_id=parent_chunk_id,
            chunk_level=chunk_level,
            window_start=window_start,
            window_end=window_end,
        )

        if self._db is not None:
            await self._persist_chunk(
                chunk_id=chunk_id,
                collection_id=collection_id,
                tenant_id=tenant_ctx.tenant_id,
                document_id=document_id,
                content=content,
                embedding=embedding,
                metadata=merged_metadata,
                chunk_index=0,
                freshness_ttl_hours=freshness_ttl_hours,
                parent_chunk_id=parent_chunk_id,
                chunk_level=chunk_level,
                window_start=window_start,
                window_end=window_end,
                window_id=window_id,
                hierarchy_level=hierarchy_level,
                is_proposition=is_proposition,
                strategy_metadata=strategy_metadata,
            )

        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is not None:
            store.chunks.append(chunk)
            store.collection.document_count = len({item.document_id for item in store.chunks})

        return chunk_id

    async def persist_index_records(
        self,
        records: list[RAGIndexRecord],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Atomically persist one complete ingestion-time strategy index."""
        if not records:
            return []
        document_ids = {record.document_id for record in records}
        if len(document_ids) != 1:
            raise ValueError("An index replacement must contain exactly one document")
        document_id = next(iter(document_ids))
        persisted = [
            {
                "chunk_id": record.chunk_id,
                "document_id": record.document_id,
                "content": record.content,
                "embedding": record.embedding,
                "metadata": {
                    **record.metadata,
                    "rag_strategy": record.strategy.value,
                    "node_type": record.strategy_metadata.get("node_type", ""),
                    "parent_chunk_id": record.parent_chunk_id,
                    "window_id": record.window_id,
                    "hierarchy_level": record.hierarchy_level,
                    "is_proposition": record.is_proposition,
                },
                "chunk_index": record.chunk_index,
                "freshness_ttl_hours": None,
                "parent_chunk_id": record.parent_chunk_id,
                "chunk_level": record.chunk_level,
                "window_start": record.window_start,
                "window_end": record.window_end,
                "window_id": record.window_id,
                "hierarchy_level": record.hierarchy_level,
                "is_proposition": record.is_proposition,
                "strategy_metadata": {
                    **record.strategy_metadata,
                    "strategy": record.strategy.value,
                },
            }
            for record in records
        ]
        if self._db is not None:
            await self._persist_chunks(
                persisted,
                collection_id=collection_id,
                tenant_id=tenant_ctx.tenant_id,
                replacement_document_id=document_id,
            )
        cache_key = (tenant_ctx.tenant_id, collection_id)
        existing = self._index_records.setdefault(cache_key, [])
        existing[:] = [
            record for record in existing if record.document_id != document_id
        ]
        existing.extend(records)
        return [record.chunk_id for record in records]

    async def search_precomputed_index(
        self,
        *,
        strategy: RAGStrategy,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Search only persisted records built for the requested strategy."""
        if self._db is None:
            return self._search_precomputed_memory(
                strategy=strategy,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
                top_k=top_k,
            )

        strategy_filter: dict[str, Any] = {"rag_strategy": strategy.value}
        if strategy.value == "agentic_chunking":
            strategy_filter["is_proposition"] = True
        candidate_limit = (
            min(top_k * 4, 100)
            if strategy.value == "agentic_chunking"
            else top_k
        )
        results = await self.hybrid_search_db(
            query,
            query_embedding,
            collection_id,
            tenant_ctx,
            top_k=candidate_limit,
            metadata_filter=strategy_filter,
            retrieval_mode="hybrid",
        )
        if strategy.value == "agentic_chunking":
            return await self._expand_agentic_parent_citations(
                results,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
                top_k=top_k,
            )
        return [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "score": result.score,
                "metadata": result.metadata,
                "citation_chunk_id": result.chunk_id,
                "citation_content": result.content,
            }
            for result in results
        ]

    def _search_precomputed_memory(
        self,
        *,
        strategy: RAGStrategy,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int,
    ) -> list[dict[str, Any]]:
        records = self._index_records.get((tenant_ctx.tenant_id, collection_id), [])
        candidates = [record for record in records if record.strategy is strategy]
        if strategy.value == "agentic_chunking":
            candidates = [record for record in candidates if record.is_proposition]
        candidate_limit = (
            min(top_k * 4, 100)
            if strategy.value == "agentic_chunking"
            else top_k
        )
        ranked = sorted(
            candidates,
            key=lambda record: (
                -(
                    _VECTOR_WEIGHT
                    * _cosine_similarity(query_embedding, record.embedding)
                    + _TRIGRAM_WEIGHT * _trigram_score(query, record.content)
                ),
                record.chunk_id,
            ),
        )[:candidate_limit]
        by_id = {record.chunk_id: record for record in records}
        from app.rag.engine import (
            ParentWindowCitation,
            RetrievalResult,
            expand_agentic_parent_results,
        )

        retrieval_results: list[RetrievalResult] = []
        parent_citations: dict[str, ParentWindowCitation] = {}
        for record in ranked:
            score = (
                _VECTOR_WEIGHT * _cosine_similarity(query_embedding, record.embedding)
                + _TRIGRAM_WEIGHT * _trigram_score(query, record.content)
            )
            retrieval_results.append(
                RetrievalResult(
                    chunk_id=record.chunk_id,
                    content=record.content,
                    score=score,
                    source_metadata=record.to_search_result(score=score)["metadata"],
                    retrieval_legs=[strategy.value],
                )
            )
            parent = by_id.get(record.parent_chunk_id or "")
            if parent is not None and record.is_proposition:
                parent_citations[record.chunk_id] = ParentWindowCitation(
                    parent.chunk_id,
                    parent.content,
                )
        if strategy.value == "agentic_chunking":
            retrieval_results = expand_agentic_parent_results(
                retrieval_results,
                parent_citations,
                top_k=top_k,
            )
        return [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "score": result.score,
                "metadata": result.source_metadata,
                "citation_chunk_id": result.chunk_id,
                "citation_content": result.content,
            }
            for result in retrieval_results[:top_k]
        ]

    async def _expand_agentic_parent_citations(
        self,
        results: list[HybridSearchResult],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not results or self._db is None:
            return []
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dimension_row = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
            if dimension_row is None:
                return []
            from app.rag.engine import (
                RetrievalResult,
                expand_agentic_parent_results,
                load_agentic_parent_citations,
            )

            citations = await load_agentic_parent_citations(
                session,
                child_chunk_ids=[result.chunk_id for result in results],
                collection_id=collection_id,
                tenant_id=tenant_ctx.tenant_id,
                embedding_dim=int(dimension_row[0]),
            )
        expanded = expand_agentic_parent_results(
            [
                RetrievalResult(
                    chunk_id=result.chunk_id,
                    content=result.content,
                    score=result.score,
                    source_metadata=dict(result.metadata),
                    retrieval_legs=["agentic_chunking"],
                    component_scores={
                        "vector": result.vector_score,
                        "trigram": result.trigram_score,
                    },
                )
                for result in results
            ],
            citations,
            top_k=top_k,
        )
        return [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "score": result.score,
                "metadata": result.source_metadata,
                "citation_chunk_id": result.chunk_id,
                "citation_content": result.content,
            }
            for result in expanded
        ]

    async def ingest_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Persist all chunks for one ingestion unit in a single transaction."""
        if not chunks:
            collection = await self.get_collection_async(
                collection_id,
                tenant_ctx=tenant_ctx,
            )
            if collection is None:
                raise KeyError(
                    f"Collection {collection_id} not found for tenant {tenant_ctx.tenant_id}"
                )
            return []
        if self._db is None:
            for chunk in chunks:
                self.ingest_chunk(chunk, collection_id=collection_id, tenant_ctx=tenant_ctx)
            return [chunk.chunk_id for chunk in chunks]

        records = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "content": chunk.content,
                "embedding": chunk.embedding,
                "metadata": dict(chunk.metadata),
                "chunk_index": chunk.chunk_index,
                "freshness_ttl_hours": None,
                "parent_chunk_id": chunk.parent_chunk_id,
                "chunk_level": chunk.chunk_level,
                "window_start": chunk.window_start,
                "window_end": chunk.window_end,
                "window_id": None,
                "hierarchy_level": 0,
                "is_proposition": False,
                "strategy_metadata": {},
            }
            for chunk in chunks
        ]
        await self._persist_chunks(
            records,
            collection_id=collection_id,
            tenant_id=tenant_ctx.tenant_id,
        )

        cached = self._data.get((tenant_ctx.tenant_id, collection_id))
        if cached is not None:
            cached.chunks.extend(chunks)
            cached.collection.document_count = len(
                {chunk.document_id for chunk in cached.chunks}
            )
        return [chunk.chunk_id for chunk in chunks]

    async def ingest_repository_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        job_id: str,
        collection_id: str,
        source_url: str,
        lease_owner: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Commit repository chunks, counters, association, and completion atomically."""
        if self._db is None:
            raise RuntimeError("Repository ingestion requires a database")
        if not chunks:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                completed = await session.execute(
                    text("""
                        UPDATE knowledge_documents AS job
                        SET status = 'completed', chunk_count = 0,
                            error_message = NULL, indexed_at = now(), heartbeat_at = NULL,
                            lease_owner = NULL, lease_expires_at = NULL
                        WHERE job.id = :job_id AND job.tenant_id = :tenant_id
                          AND job.collection_id = :collection_id
                          AND job.source_type = 'repository'
                          AND job.source_url = :source_url
                          AND job.job_source_hash = :source_hash
                          AND job.status = 'running'
                          AND job.lease_owner = :lease_owner
                          AND job.lease_expires_at > now()
                          AND job.domain_metadata->>'record_type' = 'ingestion_job'
                          AND EXISTS (
                              SELECT 1 FROM knowledge_collections AS collection
                              WHERE collection.id = job.collection_id
                                AND collection.tenant_id = job.tenant_id
                                AND collection.is_active IS TRUE
                          )
                    """),
                    {
                        "job_id": job_id,
                        "tenant_id": tenant_ctx.tenant_id,
                        "collection_id": collection_id,
                        "source_url": source_url,
                        "source_hash": hashlib.sha256(source_url.encode()).hexdigest(),
                        "lease_owner": lease_owner,
                    },
                )
                if completed.rowcount != 1:
                    raise KeyError(f"Repository ingestion job not running: {job_id}")
            return []
        records = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "content": chunk.content,
                "embedding": chunk.embedding,
                "metadata": dict(chunk.metadata),
                "chunk_index": chunk.chunk_index,
                "freshness_ttl_hours": None,
                "parent_chunk_id": chunk.parent_chunk_id,
                "chunk_level": chunk.chunk_level,
                "window_start": chunk.window_start,
                "window_end": chunk.window_end,
                "window_id": None,
                "hierarchy_level": 0,
                "is_proposition": False,
                "strategy_metadata": {"ingestion_job_id": job_id},
            }
            for chunk in chunks
        ]
        await self._persist_chunks(
            records,
            collection_id=collection_id,
            tenant_id=tenant_ctx.tenant_id,
            completion_job_id=job_id,
            completion_source_hash=hashlib.sha256(source_url.encode()).hexdigest(),
            completion_lease_owner=lease_owner,
        )
        cached = self._data.get((tenant_ctx.tenant_id, collection_id))
        if cached is not None:
            cached.chunks.extend(chunks)
            cached.collection.document_count = len(
                {chunk.document_id for chunk in cached.chunks}
            )
        return [chunk.chunk_id for chunk in chunks]

    async def _persist_chunk(
        self,
        *,
        chunk_id: str,
        collection_id: str,
        tenant_id: str,
        document_id: str,
        content: str,
        embedding: list[float],
        metadata: dict[str, Any],
        chunk_index: int,
        freshness_ttl_hours: int | None = None,
        parent_chunk_id: str | None = None,
        chunk_level: str = "leaf",
        window_start: int | None = None,
        window_end: int | None = None,
        window_id: str | None = None,
        hierarchy_level: int = 0,
        is_proposition: bool = False,
        strategy_metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._persist_chunks(
            [
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "content": content,
                    "embedding": embedding,
                    "metadata": metadata,
                    "chunk_index": chunk_index,
                    "freshness_ttl_hours": freshness_ttl_hours,
                    "parent_chunk_id": parent_chunk_id,
                    "chunk_level": chunk_level,
                    "window_start": window_start,
                    "window_end": window_end,
                    "window_id": window_id,
                    "hierarchy_level": hierarchy_level,
                    "is_proposition": is_proposition,
                    "strategy_metadata": strategy_metadata or {},
                }
            ],
            collection_id=collection_id,
            tenant_id=tenant_id,
        )

    async def _persist_chunks(
        self,
        records: list[dict[str, Any]],
        *,
        collection_id: str,
        tenant_id: str,
        completion_job_id: str | None = None,
        completion_source_hash: str | None = None,
        completion_lease_owner: str | None = None,
        replacement_document_id: str | None = None,
    ) -> None:
        if self._db is None:
            return
        if not records:
            return
        dimensions = {len(record["embedding"]) for record in records}
        if len(dimensions) != 1:
            raise ValueError("All chunks in an ingestion unit must use one embedding dimension")
        dimension = dimensions.pop()
        table = _chunk_table(dimension)
        parameters = []
        for record in records:
            vector_literal = "[" + ",".join(
                f"{value:.9g}" for value in record["embedding"]
            ) + "]"
            parameters.append(
                {
                    "id": record["chunk_id"],
                    "collection_id": collection_id,
                    "tenant_id": tenant_id,
                    "document_id": record["document_id"],
                    "content": record["content"],
                    "content_hash": hashlib.sha256(record["content"].encode()).hexdigest(),
                    "embedding": vector_literal,
                    "chunk_index": record["chunk_index"],
                    "metadata": json.dumps(record["metadata"]),
                    "parent_chunk_id": record["parent_chunk_id"],
                    "chunk_level": record["chunk_level"],
                    "window_start": record["window_start"],
                    "window_end": record["window_end"],
                    "window_id": record["window_id"],
                    "hierarchy_level": record["hierarchy_level"],
                    "is_proposition": record["is_proposition"],
                    "strategy_metadata": json.dumps(record["strategy_metadata"]),
                    "ingestion_job_id": completion_job_id,
                    "freshness_ttl_hours": record["freshness_ttl_hours"],
                }
            )

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            collection_row = (
                await session.execute(
                    text(
                        "SELECT embedding_dim, chunk_count "
                        "FROM knowledge_collections "
                        "WHERE id = :id AND tenant_id = :tid AND is_active IS TRUE "
                        "FOR UPDATE"
                    ),
                    {"id": collection_id, "tid": tenant_id},
                )
            ).fetchone()
            if collection_row is None:
                raise KeyError(f"Collection {collection_id} not found for tenant {tenant_id}")
            stored_dimension = int(collection_row[0])
            chunk_count = int(collection_row[1])
            if chunk_count and stored_dimension != dimension:
                raise ValueError(
                    f"Collection {collection_id} uses {stored_dimension}-dimensional embeddings"
                )
            if not chunk_count and stored_dimension != dimension:
                await session.execute(
                    text(
                        "UPDATE knowledge_collections SET embedding_dim = :dimension, "
                        "updated_at = now() WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"dimension": dimension, "id": collection_id, "tid": tenant_id},
                )

            if replacement_document_id is not None:
                await session.execute(
                    text(f"""
                        DELETE FROM {table}
                        WHERE collection_id = :collection_id
                          AND tenant_id = :tenant_id
                          AND document_id = :document_id
                    """),
                    {
                        "collection_id": collection_id,
                        "tenant_id": tenant_id,
                        "document_id": replacement_document_id,
                    },
                )

            await session.execute(
                text(f"""
                    INSERT INTO {table}
                        (id, collection_id, tenant_id, document_id, content,
                         content_hash, embedding, chunk_index, metadata,
                         parent_chunk_id, chunk_level, window_start, window_end,
                         window_id, hierarchy_level, is_proposition, strategy_metadata,
                         expires_at, ingestion_job_id)
                    VALUES
                        (:id, :collection_id, :tenant_id, :document_id, :content,
                         :content_hash, CAST(:embedding AS vector), :chunk_index,
                         CAST(:metadata AS jsonb), :parent_chunk_id, :chunk_level,
                         :window_start, :window_end, :window_id, :hierarchy_level,
                         :is_proposition, CAST(:strategy_metadata AS jsonb),
                         CASE WHEN CAST(:freshness_ttl_hours AS integer) IS NULL THEN NULL
                              ELSE now() + (
                                  CAST(:freshness_ttl_hours AS integer) * interval '1 hour'
                              ) END, :ingestion_job_id)
                """),
                parameters,
            )
            await session.execute(
                text(f"""
                    UPDATE knowledge_collections
                    SET chunk_count = (
                            SELECT count(*) FROM {table}
                            WHERE collection_id = :id AND tenant_id = :tid
                        ),
                        document_count = (
                            SELECT count(DISTINCT document_id) FROM {table}
                            WHERE collection_id = :id AND tenant_id = :tid
                        ),
                        total_size_bytes = (
                            SELECT COALESCE(sum(octet_length(content)), 0)
                            FROM {table}
                            WHERE collection_id = :id AND tenant_id = :tid
                        ),
                        last_indexed_at = now(),
                        updated_at = now()
                    WHERE id = :id AND tenant_id = :tid
                """),
                {"id": collection_id, "tid": tenant_id},
            )
            if completion_job_id is not None:
                completed = await session.execute(
                    text("""
                        UPDATE knowledge_documents
                        SET status = 'completed',
                            chunk_count = :chunk_count,
                            error_message = NULL,
                            indexed_at = now(), heartbeat_at = NULL,
                            lease_owner = NULL, lease_expires_at = NULL
                        WHERE id = :job_id AND tenant_id = :tenant_id
                          AND collection_id = :collection_id
                          AND source_type = 'repository'
                          AND job_source_hash = :source_hash
                          AND status = 'running'
                          AND lease_owner = :lease_owner
                          AND lease_expires_at > now()
                          AND domain_metadata->>'record_type' = 'ingestion_job'
                    """),
                    {
                        "job_id": completion_job_id,
                        "tenant_id": tenant_id,
                        "collection_id": collection_id,
                        "source_hash": completion_source_hash,
                        "lease_owner": completion_lease_owner,
                        "chunk_count": len(records),
                    },
                )
                if completed.rowcount != 1:
                    raise KeyError(f"Repository ingestion job not running: {completion_job_id}")

    async def _db_ingest_with_citations(
        self,
        *,
        chunk_id: str,
        collection_id: str,
        content: str,
        embedding: list[float],
        metadata: dict[str, Any],
        tenant_id: str,
        source_url: str,
        source_type: str,
        source_doc_id: str,
        page_number: int | None,
        freshness_ttl_hours: int,
        content_hash: str,
        parent_chunk_id: str | None = None,
        chunk_level: str = "leaf",
        window_start: int | None = None,
        window_end: int | None = None,
        window_id: str | None = None,
        hierarchy_level: int = 0,
        is_proposition: bool = False,
        strategy_metadata: dict[str, Any] | None = None,
    ) -> None:
        del content_hash
        full_metadata = dict(metadata)
        full_metadata.setdefault("source_url", source_url)
        full_metadata.setdefault("source_type", source_type)
        full_metadata.setdefault("source_doc_id", source_doc_id or chunk_id)
        full_metadata.setdefault("page_number", page_number)
        full_metadata.setdefault("freshness_ttl_hours", freshness_ttl_hours)
        await self._persist_chunk(
            chunk_id=chunk_id,
            collection_id=collection_id,
            tenant_id=tenant_id,
            document_id=source_doc_id or chunk_id,
            content=content,
            embedding=embedding,
            metadata=full_metadata,
            chunk_index=0,
            freshness_ttl_hours=freshness_ttl_hours,
            parent_chunk_id=parent_chunk_id,
            chunk_level=chunk_level,
            window_start=window_start,
            window_end=window_end,
            window_id=window_id,
            hierarchy_level=hierarchy_level,
            is_proposition=is_proposition,
            strategy_metadata=strategy_metadata,
        )

    async def sync_from_db(self) -> int:
        """Compatibility no-op; persisted metadata is read per tenant on demand."""
        return 0

    async def expand_to_parents(
        self,
        child_chunk_ids: list[str],
        collection_id: str,
        tenant_ctx: TenantContext,
        db: Any = None,
    ) -> list[Any]:
        """Expand child chunk IDs to their parent chunks for full-context retrieval.

        Fetches the parent chunk for each child chunk ID. When a child has no
        persisted parent, the child itself is returned.
        """
        if not child_chunk_ids:
            return []
        _db = db or self._db
        if _db is None:
            return []
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            _db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dimension_row = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
            if dimension_row is None:
                return []
            table = _chunk_table(int(dimension_row[0]))
            rows = (
                await session.execute(
                    text(f"""
                        SELECT COALESCE(parent.id, child.id),
                               COALESCE(parent.content, child.content),
                               COALESCE(parent.metadata->>'source_url',
                                        child.metadata->>'source_url', ''),
                               COALESCE(parent.metadata, child.metadata)
                        FROM {table} AS child
                        LEFT JOIN {table} AS parent
                          ON parent.id = child.parent_chunk_id
                         AND parent.collection_id = child.collection_id
                         AND parent.tenant_id = child.tenant_id
                        WHERE child.id = ANY(:child_ids)
                          AND child.collection_id = :cid
                          AND child.tenant_id = :tid
                    """),
                    {
                        "child_ids": child_chunk_ids,
                        "cid": collection_id,
                        "tid": tenant_ctx.tenant_id,
                    },
                )
            ).fetchall()

        return [
            type(
                "Chunk",
                (),
                {
                    "chunk_id": str(row[0]),
                    "content": str(row[1]),
                    "source_url": str(row[2] or ""),
                    "metadata": row[3] or {},
                    "score": 0.8,
                },
            )()
            for row in rows
        ]
