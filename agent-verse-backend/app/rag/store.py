"""In-memory knowledge store with hybrid search (cosine 70% + trigram 30%).

In production this is backed by PostgreSQL + pgvector (HNSW index) and pg_trgm.
This pure-Python implementation is used in tests and as a fallback.

Hybrid search formula:
  score = 0.7 * cosine_similarity(query_vec, chunk_vec)
         + 0.3 * trigram_overlap(query_text, chunk_text)

When ``db_session_factory`` is supplied, writes are also persisted to
PostgreSQL via fire-and-forget asyncio tasks. A ``hybrid_search_db()`` async
method performs server-side hybrid search using pgvector + pg_trgm when a
DB session is available.
"""

from __future__ import annotations

import asyncio
import math
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.rag.models import Chunk, KnowledgeCollection
from app.tenancy.context import TenantContext

_VECTOR_WEIGHT = 0.7
_TRIGRAM_WEIGHT = 0.3

_log = get_logger(__name__)


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
    """In-memory implementation of the knowledge store.

    Each collection is namespaced by (tenant_id, collection_id).

    When ``db_session_factory`` is provided, mutations are also persisted to
    PostgreSQL via fire-and-forget asyncio tasks. DB failures are logged as
    warnings and never raised to callers.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        # Key: (tenant_id, collection_id) → _CollectionStore
        self._data: dict[tuple[str, str], _CollectionStore] = {}
        self._db = db_session_factory

    def create_collection(
        self, collection: KnowledgeCollection, *, tenant_ctx: TenantContext
    ) -> str:
        key = (tenant_ctx.tenant_id, collection.collection_id)
        self._data[key] = _CollectionStore(collection=collection)
        if self._db is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._db_create_collection(collection, tenant_ctx.tenant_id)
                )
            except RuntimeError:
                pass  # No running loop (e.g., in sync test context)
        return collection.collection_id

    async def _db_create_collection(
        self, collection: KnowledgeCollection, tenant_id: str
    ) -> None:
        if self._db is None:
            return
        try:
            from app.db.models.knowledge import KnowledgeCollection as KCModel
            from app.db.rls import sqlalchemy_rls_context

            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_id):
                    row = KCModel(
                        id=collection.collection_id,
                        tenant_id=tenant_id,
                        name=collection.name,
                        description=collection.description,
                        embedder=collection.embedder,
                        document_count=0,
                    )
                    session.add(row)
        except Exception as exc:
            _log.warning("DB create collection failed: %s", exc)

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
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is None:
            raise KeyError(
                f"Collection {collection_id} not found for tenant {tenant_ctx.tenant_id}"
            )
        store.chunks.append(chunk)
        store.collection.document_count = len({c.document_id for c in store.chunks})
        if self._db is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._db_ingest_chunk(chunk, collection_id, tenant_ctx.tenant_id)
                )
            except RuntimeError:
                pass

    async def _db_ingest_chunk(
        self, chunk: Chunk, collection_id: str, tenant_id: str
    ) -> None:
        if self._db is None:
            return
        try:
            import hashlib

            from app.db.models.knowledge import Document
            from app.db.rls import sqlalchemy_rls_context

            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_id):
                    row = Document(
                        id=chunk.chunk_id,
                        collection_id=collection_id,
                        tenant_id=tenant_id,
                        source="ingestion",
                        content=chunk.content,
                        content_hash=hashlib.sha256(
                            chunk.content.encode()
                        ).hexdigest(),
                        embedding=chunk.embedding,  # pgvector column
                        chunk_index=chunk.chunk_index,
                        doc_metadata=chunk.metadata,
                    )
                    session.add(row)
        except Exception as exc:
            _log.warning("DB ingest chunk failed: %s", exc)

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
        """PostgreSQL hybrid search via the RRF-fused retrieval engine.

        Delegates to ``app.rag.engine.hybrid_search`` which runs three parallel
        retrieval legs (pgvector ANN + FTS + pg_trgm) and fuses them with
        Reciprocal Rank Fusion.  Results are mapped back to ``HybridSearchResult``
        for API compatibility.

        Falls back to in-memory ``hybrid_search()`` if DB is not available or
        if the engine call fails.
        """
        if self._db is None:
            return self.hybrid_search(
                query, query_embedding, collection_id, tenant_ctx, top_k,
                metadata_filter=metadata_filter,
            )

        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context
            from app.rag.engine import RetrievalResult
            from app.rag.engine import hybrid_search as _engine_search

            # Determine embedding dimension for correct table routing.
            embedding_dim: int | None = None
            async with self._db() as session, sqlalchemy_rls_context(
                session, tenant_ctx.tenant_id
            ):
                try:
                    col_res = await session.execute(
                        text(
                            "SELECT embedding_dim FROM knowledge_collections "
                            "WHERE id = :cid AND tenant_id = :tid"
                        ),
                        {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                    )
                    col_row = col_res.fetchone()
                    if col_row and col_row[0] in (768, 1024, 1536, 3072):
                        embedding_dim = int(col_row[0])
                except Exception:
                    pass

                engine_results: list[RetrievalResult] = await _engine_search(
                    session=session,
                    query=query,
                    query_embedding=query_embedding or None,
                    collection_id=collection_id,
                    top_k=top_k,
                    retrieval_mode="hybrid",
                    embedding_dim=embedding_dim,
                )

            # Post-filter by metadata (Python-side JSONB subset match)
            if metadata_filter:
                engine_results = [
                    r for r in engine_results
                    if all(r.source_metadata.get(k) == v for k, v in metadata_filter.items())
                ]

            return [
                HybridSearchResult(
                    chunk_id=r.chunk_id,
                    content=r.content,
                    score=r.score,
                    vector_score=0.0,   # RRF fused — per-leg scores not exposed
                    trigram_score=0.0,
                    source_url=str(r.source_metadata.get("source_url", "") or ""),
                    source_doc_id=str(r.source_metadata.get("source_doc_id", "") or ""),
                    page_number=r.source_metadata.get("page_number"),
                    metadata=r.source_metadata,
                )
                for r in engine_results
            ]
        except Exception as exc:
            _log.warning("DB hybrid search failed, falling back to in-memory: %s", exc)
            return self.hybrid_search(query, query_embedding, collection_id, tenant_ctx, top_k)

    async def search(
        self,
        query: str,
        collection_id: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Duck-typed search adapter for ``federated_search``.

        Runs the in-memory hybrid search (without embeddings) and returns
        results as plain dicts compatible with the federated search pipeline.
        This method satisfies the ``store.search(query, cid, top_k)`` protocol
        expected by ``app.knowledge.federated_search.federated_search``.
        """
        # Build a minimal tenant context from collections we have in memory.
        results: list[dict[str, Any]] = []
        for (_tid, cid), store in self._data.items():
            if cid != collection_id:
                continue
            for chunk in store.chunks:
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
        """Delete document from in-memory store and DB."""
        count = self.delete_document(
            document_id, collection_id=collection_id, tenant_ctx=tenant_ctx
        )
        _db = db or self._db
        if _db is not None:
            try:
                from sqlalchemy import text
                async with _db() as session, session.begin():
                    result = await session.execute(
                        text(
                            "DELETE FROM knowledge_chunks_1536 "
                            "WHERE document_id = :did AND collection_id = :cid"
                        ),
                        {"did": document_id, "cid": collection_id},
                    )
                    count = max(count, result.rowcount or 0)
            except Exception as exc:
                _log.warning("delete_document_async_db_failed: %s", exc)
        return count

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
    ) -> str:
        """Ingest a single content chunk with citation metadata.

        Creates an embedding (if an embedder is provided), stores the chunk
        in-memory, and persists to PostgreSQL with the citation fields
        (source_url, source_type, source_doc_id, page_number, freshness_ttl_hours).

        Returns the new chunk_id (UUID hex string).
        """
        import hashlib

        chunk_id = _uuid.uuid4().hex
        embedding: list[float] = []
        if embedder is not None:
            try:
                from app.providers.base import embed_texts
                embeddings = await embed_texts([content], provider=embedder)
                embedding = embeddings[0]
            except Exception as exc:
                _log.warning("ingest_document_embed_failed: %s", exc)

        merged_metadata = dict(metadata or {})
        merged_metadata.update({
            "source_url": source_url,
            "source_type": source_type,
            "source_doc_id": source_doc_id,
            "page_number": page_number,
        })

        chunk = Chunk(
            document_id=source_doc_id or chunk_id,
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

        # In-memory storage
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is not None:
            store.chunks.append(chunk)
            store.collection.document_count = len({c.document_id for c in store.chunks})

        # DB persistence with citation fields
        if self._db is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._db_ingest_with_citations(
                        chunk_id=chunk_id,
                        collection_id=collection_id,
                        content=content,
                        embedding=embedding,
                        metadata=merged_metadata,
                        tenant_id=tenant_ctx.tenant_id,
                        source_url=source_url,
                        source_type=source_type,
                        source_doc_id=source_doc_id,
                        page_number=page_number,
                        freshness_ttl_hours=freshness_ttl_hours,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        parent_chunk_id=parent_chunk_id,
                        chunk_level=chunk_level,
                        window_start=window_start,
                        window_end=window_end,
                    )
                )
            except RuntimeError:
                pass  # No running loop (sync context)

        return chunk_id

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
    ) -> None:
        """Persist a document chunk to the correct ``knowledge_chunks_{dim}`` table.

        FIX 4.3: Writes to ``knowledge_chunks_{dim}`` instead of the legacy
        ``documents`` table.  The embedding dimension is resolved by querying
        ``knowledge_collections``; falls back to 1536 when unknown.

        Citation fields (source_url, source_type, source_doc_id, page_number,
        freshness_ttl_hours) are stored in the metadata JSONB column since the
        dynamic-dimension tables do not have dedicated citation columns.

        FIX 7: Embedding is formatted as an explicit PostgreSQL vector literal
        ``[v1.000000,v2.000000,...]`` instead of Python's ``str(list)``.
        """
        if self._db is None:
            return
        try:
            import json

            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # FIX 7: PostgreSQL vector literal — explicit 6 decimal places.
            emb_str = (
                "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
                if embedding
                else None
            )

            # Merge citation fields into metadata JSONB.
            full_metadata = dict(metadata)
            full_metadata.setdefault("source_url", source_url)
            full_metadata.setdefault("source_type", source_type)
            full_metadata.setdefault("source_doc_id", source_doc_id)
            full_metadata.setdefault("page_number", page_number)
            full_metadata.setdefault("freshness_ttl_hours", freshness_ttl_hours)

            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_id):
                    # FIX 4.3: Resolve embedding dimension → correct table.
                    dim = 1536
                    try:
                        dim_res = await session.execute(
                            text(
                                "SELECT embedding_dim FROM knowledge_collections "
                                "WHERE id = :cid AND tenant_id = :tid"
                            ),
                            {"cid": collection_id, "tid": tenant_id},
                        )
                        dim_row = dim_res.fetchone()
                        if dim_row and dim_row[0] in (768, 1024, 1536, 3072):
                            dim = int(dim_row[0])
                    except Exception:
                        pass

                    table_name = f"knowledge_chunks_{dim}"
                    try:
                        await session.execute(
                            text(f"""
                                INSERT INTO {table_name}
                                    (id, collection_id, tenant_id, content, content_hash,
                                     embedding, chunk_index, metadata,
                                     parent_chunk_id, chunk_level, window_start, window_end)
                                VALUES
                                    (:id, :cid, :tid, :content, :hash,
                                     :emb::vector, 0, :meta::jsonb,
                                     :parent_chunk_id, :chunk_level, :window_start, :window_end)
                                ON CONFLICT (id) DO NOTHING
                            """),
                            {
                                "id": chunk_id,
                                "cid": collection_id,
                                "tid": tenant_id,
                                "content": content,
                                "hash": content_hash,
                                "emb": emb_str,
                                "meta": json.dumps(full_metadata),
                                "parent_chunk_id": parent_chunk_id,
                                "chunk_level": chunk_level,
                                "window_start": window_start,
                                "window_end": window_end,
                            },
                        )
                    except Exception:
                        # Fallback: insert without parent/window columns (pre-migration)
                        await session.execute(
                            text(f"""
                                INSERT INTO {table_name}
                                    (id, collection_id, tenant_id, content, content_hash,
                                     embedding, chunk_index, metadata)
                                VALUES
                                    (:id, :cid, :tid, :content, :hash,
                                     :emb::vector, 0, :meta::jsonb)
                                ON CONFLICT (id) DO NOTHING
                            """),
                            {
                                "id": chunk_id,
                                "cid": collection_id,
                                "tid": tenant_id,
                                "content": content,
                                "hash": content_hash,
                                "emb": emb_str,
                                "meta": json.dumps(full_metadata),
                            },
                        )
        except Exception as exc:
            _log.warning("DB ingest with citations failed: %s", exc)

    async def sync_from_db(self) -> int:
        """Load collections and chunks from PostgreSQL into memory.

        FIX 3: Replaced the hard document cap with cursor-based streaming in
        batches of 1000 so all chunks are loaded regardless of total count.
        Memory growth is bounded per-batch; the old safety cap silently dropped
        data for large tenants and was removed.

        Returns the number of new chunks loaded.
        Returns 0 immediately when no ``db_session_factory`` is configured.
        """
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.knowledge import Document
            from app.db.models.knowledge import KnowledgeCollection as KCModel
            from app.db.models.tenant import Tenant
            from app.rag.models import Chunk, KnowledgeCollection

            loaded = 0
            async with self._db() as session:
                # Load collections — only for active tenants (no cap needed here
                # as collection count is naturally bounded).
                col_result = await session.execute(
                    select(KCModel)
                    .join(Tenant, KCModel.tenant_id == Tenant.id)
                    .where(Tenant.is_active == True)  # noqa: E712
                    .limit(10_000)
                )
                collections = col_result.scalars().all()
                for c in collections:
                    col = KnowledgeCollection(
                        name=c.name,
                        description=c.description or "",
                        collection_id=c.id,
                        document_count=c.document_count or 0,
                        embedder=c.embedder or "voyage",
                    )
                    key = (c.tenant_id, c.id)
                    if key not in self._data:
                        self._data[key] = _CollectionStore(collection=col)

                # FIX 3: Stream documents in batches of 1000 — no hard cap.
                # Uses OFFSET pagination ordered by primary key for stable pages.
                if collections:
                    col_ids = [c.id for c in collections]
                    batch_size = 1000
                    offset = 0

                    while True:
                        batch_result = await session.execute(
                            select(Document)
                            .where(Document.collection_id.in_(col_ids))
                            .order_by(Document.id)   # stable ordering for pagination
                            .limit(batch_size)
                            .offset(offset)
                        )
                        docs = batch_result.scalars().all()
                        if not docs:
                            break

                        for d in docs:
                            key = (d.tenant_id, d.collection_id)
                            cstore = self._data.get(key)
                            if cstore is not None:
                                existing_ids = {c.chunk_id for c in cstore.chunks}
                                if d.id not in existing_ids:
                                    chunk = Chunk(
                                        document_id=d.id,
                                        content=d.content,
                                        embedding=list(d.embedding) if d.embedding is not None else [],
                                        chunk_index=d.chunk_index or 0,
                                        chunk_id=d.id,
                                        metadata=dict(d.doc_metadata or {}),
                                    )
                                    cstore.chunks.append(chunk)
                                    loaded += 1

                        offset += batch_size

            _log.info(
                "Synced %d chunks from DB into KnowledgeStore (streaming, no cap)", loaded
            )
            return loaded
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

        Fetches the parent chunk for each child chunk ID.  When a child has no
        parent (chunk_level == 'leaf'), the child itself is returned.  Falls
        back to an empty list on any DB error.
        """
        if not child_chunk_ids:
            return []
        _db = db or self._db
        if _db is None:
            return []
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with _db() as session, sqlalchemy_rls_context(
                session, tenant_ctx.tenant_id
            ):
                rows = (
                    await session.execute(
                        text("""
                            SELECT c.id, c.content,
                                   COALESCE(c.metadata->>'source_url', '') AS source_url,
                                   c.metadata
                            FROM knowledge_chunks_1536 c
                            INNER JOIN knowledge_chunks_1536 child
                                ON child.parent_chunk_id = c.id
                            WHERE child.id = ANY(:child_ids)
                              AND c.collection_id = :cid
                            UNION
                            SELECT id, content,
                                   COALESCE(metadata->>'source_url', '') AS source_url,
                                   metadata
                            FROM knowledge_chunks_1536
                            WHERE id = ANY(:child_ids)
                              AND collection_id = :cid
                        """),
                        {
                            "child_ids": child_chunk_ids,
                            "cid": collection_id,
                        },
                    )
                ).fetchall()

            return [
                type("Chunk", (), {
                    "chunk_id": str(r[0]),
                    "content": str(r[1]),
                    "source_url": str(r[2] or ""),
                    "metadata": r[3] or {},
                    "score": 0.8,
                })()
                for r in rows
            ]
        except Exception as exc:
            _log.warning("expand_to_parents failed: %s", exc)
            return []
