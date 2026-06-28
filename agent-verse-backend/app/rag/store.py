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
    metadata: dict = field(default_factory=dict)


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
    ) -> list[HybridSearchResult]:
        """In-memory hybrid search (fast path, always available)."""
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        if store is None:
            return []

        scored: list[HybridSearchResult] = []
        for chunk in store.chunks:
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
        return scored[:top_k]

    async def hybrid_search_db(
        self,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[HybridSearchResult]:
        """PostgreSQL hybrid search using pgvector cosine + pg_trgm.

        FIX 1: Explicit ``tenant_id`` filter in WHERE clause (defence-in-depth
                on top of RLS — guards against RLS misconfiguration).
        FIX 2: Variable embedding dimensions — queries ``knowledge_chunks_{dim}``
                when the collection is registered in ``knowledge_collections``;
                falls back to the legacy ``documents`` table otherwise.
        FIX 7: Embedding passed as a PostgreSQL vector literal
                (``[v1,v2,...]``) instead of Python's ``str(list)`` which can
                produce inconsistent float formatting.

        Falls back to in-memory ``hybrid_search()`` if DB is not available or
        if the query fails.
        """
        if self._db is None:
            return self.hybrid_search(query, query_embedding, collection_id, tenant_ctx, top_k)

        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # FIX 7: Explicit PostgreSQL vector literal format for precision.
            qvec_str = (
                "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
                if query_embedding
                else None
            )
            if qvec_str is None:
                return self.hybrid_search(query, query_embedding, collection_id, tenant_ctx, top_k)

            async with self._db() as session:
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    # FIX 2: Detect collection's embedding dimension and pick
                    # the appropriate knowledge_chunks_{dim} table.
                    table = "documents"  # legacy fallback
                    try:
                        col_res = await session.execute(
                            text(
                                "SELECT embedding_dim FROM knowledge_collections "
                                "WHERE id = :cid AND tenant_id = :tid"
                            ),
                            {
                                "cid": collection_id,
                                "tid": tenant_ctx.tenant_id,
                            },
                        )
                        col_row = col_res.fetchone()
                        if col_row and col_row.embedding_dim in (768, 1024, 1536, 3072):
                            table = f"knowledge_chunks_{col_row.embedding_dim}"
                    except Exception:
                        pass  # keep using legacy 'documents' table

                    # Build SELECT — legacy table has extra citation columns;
                    # knowledge_chunks_* tables surface them via metadata JSONB.
                    if table == "documents":
                        extra_cols = (
                            "COALESCE(source_url, '') AS source_url,\n"
                            "                            COALESCE(source_doc_id, '') AS source_doc_id,\n"
                            "                            page_number,"
                        )
                    else:
                        extra_cols = (
                            "'' AS source_url,\n"
                            "                            '' AS source_doc_id,\n"
                            "                            NULL::integer AS page_number,"
                        )

                    # FIX 1: Add explicit tenant_id to WHERE clause.
                    sql = text(f"""
                        SELECT
                            id AS chunk_id,
                            content,
                            (0.7 * (1 - (embedding <=> :qvec::vector)) +
                             0.3 * similarity(content, :query)) AS score,
                            (1 - (embedding <=> :qvec::vector)) AS vector_score,
                            similarity(content, :query) AS trigram_score,
                            {extra_cols}
                            COALESCE(metadata, '{{}}') AS metadata
                        FROM {table}
                        WHERE collection_id = :cid
                          AND tenant_id = :tid
                        ORDER BY score DESC
                        LIMIT :k
                    """)
                    result = await session.execute(sql, {
                        "qvec": qvec_str,     # FIX 7: vector literal
                        "query": query,
                        "cid": collection_id,
                        "tid": tenant_ctx.tenant_id,  # FIX 1: explicit tenant filter
                        "k": top_k,
                    })
                    rows = result.fetchall()
                    return [
                        HybridSearchResult(
                            chunk_id=str(r.chunk_id),
                            content=r.content,
                            score=float(r.score or 0),
                            vector_score=float(r.vector_score or 0),
                            trigram_score=float(r.trigram_score or 0),
                            source_url=getattr(r, "source_url", "") or "",
                            source_doc_id=getattr(r, "source_doc_id", "") or "",
                            page_number=getattr(r, "page_number", None),
                            metadata=dict(getattr(r, "metadata", {}) or {}),
                        )
                        for r in rows
                    ]
        except Exception as exc:
            _log.warning("DB hybrid search failed, falling back to in-memory: %s", exc)
            return self.hybrid_search(query, query_embedding, collection_id, tenant_ctx, top_k)

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
    ) -> None:
        """Persist a document chunk with citation fields to PostgreSQL.

        FIX 7: Embedding is formatted as an explicit PostgreSQL vector literal
        ``[v1.000000,v2.000000,...]`` instead of Python's ``str(list)`` which
        can produce ``[0.1, 0.2, ...]`` — a format that differs from pgvector's
        expected ``[0.100000,0.200000,...]`` and may cause cast failures.
        """
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # FIX 7: PostgreSQL vector literal — explicit 6 decimal places.
            emb_str = (
                "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
                if embedding
                else None
            )

            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_id):
                    await session.execute(
                        text("""
                            INSERT INTO documents
                                (id, collection_id, tenant_id, source, content, content_hash,
                                 embedding, chunk_index, metadata,
                                 source_url, source_type, source_doc_id,
                                 page_number, freshness_ttl_hours)
                            VALUES
                                (:id, :cid, :tid, :src, :content, :hash,
                                 :emb::vector, 0, :meta::jsonb,
                                 :source_url, :source_type, :source_doc_id,
                                 :page_number, :ttl)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": chunk_id,
                            "cid": collection_id,
                            "tid": tenant_id,
                            "src": source_type,
                            "content": content,
                            "hash": content_hash,
                            "emb": emb_str,          # FIX 7: vector literal
                            "meta": str(metadata),
                            "source_url": source_url,
                            "source_type": source_type,
                            "source_doc_id": source_doc_id,
                            "page_number": page_number,
                            "ttl": freshness_ttl_hours,
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
