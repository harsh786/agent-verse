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
from typing import Any

from app.observability.logging import get_logger
from app.rag.models import Chunk, KnowledgeCollection
from app.tenancy.context import TenantContext

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
        if self._db is None:
            return self.create_collection(collection, tenant_ctx=tenant_ctx)
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
                retrieval_mode="hybrid",
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
        tenant_ctx: TenantContext | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return plain-dict results from the configured source of truth."""
        if self._db is not None:
            if tenant_ctx is None:
                raise TypeError("tenant_ctx is required for persisted knowledge search")
            persisted = await self.hybrid_search_db(
                query,
                [],
                collection_id,
                tenant_ctx,
                top_k=top_k,
                metadata_filter=metadata_filter,
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
            if tenant_ctx is not None and stored_tenant_id != tenant_ctx.tenant_id:
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
        if embedder is not None:
            from app.providers.base import embed_texts

            try:
                embeddings = await embed_texts([content], provider=embedder)
                embedding = embeddings[0]
            except Exception as exc:
                if self._db is not None:
                    raise
                _log.warning("ingest_document_embed_failed: %s", exc)

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

    async def ingest_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Persist all chunks for one ingestion unit in a single transaction."""
        if not chunks:
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

            await session.execute(
                text(f"""
                    INSERT INTO {table}
                        (id, collection_id, tenant_id, document_id, content,
                         content_hash, embedding, chunk_index, metadata,
                         parent_chunk_id, chunk_level, window_start, window_end,
                         window_id, hierarchy_level, is_proposition, strategy_metadata,
                         expires_at)
                    VALUES
                        (:id, :collection_id, :tenant_id, :document_id, :content,
                         :content_hash, CAST(:embedding AS vector), :chunk_index,
                         CAST(:metadata AS jsonb), :parent_chunk_id, :chunk_level,
                         :window_start, :window_end, :window_id, :hierarchy_level,
                         :is_proposition, CAST(:strategy_metadata AS jsonb),
                         CASE WHEN CAST(:freshness_ttl_hours AS integer) IS NULL THEN NULL
                              ELSE now() + (
                                  CAST(:freshness_ttl_hours AS integer) * interval '1 hour'
                              ) END)
                """),
                parameters,
            )
            await session.execute(
                text(f"""
                    UPDATE knowledge_collections
                    SET chunk_count = (
                            SELECT count(*) FROM {table} WHERE collection_id = :id
                        ),
                        document_count = (
                            SELECT count(DISTINCT document_id) FROM {table}
                            WHERE collection_id = :id
                        ),
                        total_size_bytes = (
                            SELECT COALESCE(sum(octet_length(content)), 0)
                            FROM {table} WHERE collection_id = :id
                        ),
                        last_indexed_at = now(),
                        updated_at = now()
                    WHERE id = :id AND tenant_id = :tid
                """),
                {"id": collection_id, "tid": tenant_id},
            )

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
        """Load compatibility collection metadata without hydrating chunk text."""
        if self._db is None:
            return 0
        try:
            from sqlalchemy import text

            from app.rag.models import KnowledgeCollection

            async with self._db() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT collection.id, collection.tenant_id, collection.name, "
                            "collection.description, collection.document_count, "
                            "collection.embedder FROM knowledge_collections AS collection "
                            "JOIN tenants AS tenant ON tenant.id = collection.tenant_id "
                            "WHERE tenant.is_active IS TRUE AND collection.is_active IS TRUE "
                            "ORDER BY collection.id LIMIT 10000"
                        )
                    )
                ).fetchall()
                for row in rows:
                    key = (str(row[1]), str(row[0]))
                    if key in self._data:
                        continue
                    self._data[key] = _CollectionStore(
                        collection=KnowledgeCollection(
                            name=str(row[2]),
                            description=str(row[3] or ""),
                            collection_id=str(row[0]),
                            document_count=int(row[4] or 0),
                            embedder=str(row[5] or "voyage"),
                        )
                    )

            _log.info("Synced %d collection metadata records into KnowledgeStore", len(rows))
            return 0
        except Exception as exc:
            _log.warning("DB knowledge sync failed: %s", exc)
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
