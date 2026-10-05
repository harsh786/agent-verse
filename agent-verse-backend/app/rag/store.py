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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from app.observability.logging import get_logger
from app.rag.models import Chunk, KnowledgeCollection
from app.tenancy.context import PlanTier, TenantContext

if TYPE_CHECKING:
    from app.rag.contracts import RAGStrategy
    from app.rag.indexing import RAGIndexRecord

_VECTOR_WEIGHT = 0.7
_TRIGRAM_WEIGHT = 0.3
SUPPORTED_EMBEDDING_DIMENSIONS = (768, 1024, 1536, 2048, 3072)
# Label of a collection whose embedder is not known — never a guessed vendor.
_UNKNOWN_EMBEDDER = "unknown"

_log = get_logger(__name__)


class EmbeddingDimensionError(ValueError):
    """An embedding width has no chunk table, or disagrees with a collection."""


# Chunks removed per transaction when a collection is deleted: one cascading
# DELETE over a large collection held row locks on the whole chunk table's
# rows for the collection and produced one huge WAL burst.
_COLLECTION_DELETE_BATCH = 1000


def _batch_embedding_model(records: list[dict[str, Any]]) -> str | None:
    """The embedding model a batch of chunk records names, if any (pipeline metadata)."""
    for record in records:
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        effective = str(metadata.get("embedding_model_effective") or "").strip()
        if effective:
            # "default": the orchestrator fell back to the deployment embedder,
            # so its *selected* model id (``embedding_model``) was not used.
            return None if effective == "default" else effective[:200]
        model = str(metadata.get("embedding_model") or "").strip()
        if model:
            return model[:200]
    return None


def _chunk_table(dimension: int) -> str:
    if dimension not in SUPPORTED_EMBEDDING_DIMENSIONS:
        raise EmbeddingDimensionError(
            f"Unsupported embedding dimension: {dimension} (supported: "
            f"{', '.join(str(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS)}); "
            "configure an embedding model with a supported output dimension"
        )
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


class KnowledgeLegalHoldError(RuntimeError):
    """A deletion was refused: the collection, a document in it, or the tenant is held."""


# An in-force legal hold covering collection :cid of tenant :tid — tenant-wide,
# on the collection id, or on ANY document of the collection (the same coverage
# as app.rag.retention._NOT_UNDER_LEGAL_HOLD). Driven from the (few) holds: each
# held resource id probes the chunk table's (collection_id, document_id, ...)
# unique index, so the check never scans a large collection.
_COLLECTION_HELD_SQL = (
    "SELECT 1 FROM legal_holds lh WHERE lh.tenant_id = :tid "
    "AND lh.status = 'active' AND (lh.expires_at IS NULL OR lh.expires_at > now()) "
    "AND (lh.resource_type = 'tenant' "
    "OR lh.resource_ids @> jsonb_build_array(CAST(:cid AS text)) "
    "{documents}) LIMIT 1"
)
_HELD_DOCUMENT_SQL = (
    "OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(lh.resource_ids) AS r(rid) "
    "JOIN {table} c ON c.collection_id = :cid AND c.document_id = r.rid "
    "AND c.tenant_id = :tid)"
)


# The first of :ids (documents about to be replaced) that HAS chunks in the
# collection and is covered by an in-force hold (document, collection, tenant).
_REPLACED_HELD_SQL = (
    "SELECT d.rid FROM unnest(CAST(:ids AS text[])) AS d(rid) "
    "WHERE EXISTS (SELECT 1 FROM {table} c WHERE c.collection_id = :cid "
    "AND c.tenant_id = :tid AND c.document_id = d.rid) "
    "AND EXISTS (SELECT 1 FROM legal_holds lh WHERE lh.tenant_id = :tid "
    "AND lh.status = 'active' AND (lh.expires_at IS NULL OR lh.expires_at > now()) "
    "AND (lh.resource_type = 'tenant' "
    "OR lh.resource_ids @> jsonb_build_array(CAST(:cid AS text)) "
    "OR lh.resource_ids @> jsonb_build_array(d.rid))) LIMIT 1"
)


def _collection_held_sql(table: str | None) -> str:
    documents = _HELD_DOCUMENT_SQL.format(table=table) if table else ""
    return _COLLECTION_HELD_SQL.format(documents=documents)


class EmbeddingProviderUnavailableError(RuntimeError):
    """A vector ingestion request has no usable embedding provider."""


class DuplicateContentError(RuntimeError):
    """A concurrent/retried ingestion lost the race to index this content.

    Raised from ``_persist_chunks`` when, under the collection's row lock, a
    chunk carrying the same ``doc_content_hash`` is already persisted for this
    tenant/collection. Closes the TOCTOU window between the caller's earlier
    ``exists_by_hash`` check (Stage 3 of the ingestion pipeline, or the RPA/OCR
    equivalent) and the later write: two concurrent identical ingestions (a
    retry racing the original attempt, or a re-sync overlapping a manual sync)
    can both observe "not yet indexed" before either commits. The loser should
    be treated as an idempotent no-op dedup skip, not a hard failure.
    """


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
        return {s[i : i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()

    q_tris = trigrams(query)
    t_tris = trigrams(text)
    if not q_tris:
        return 0.0
    overlap = len(q_tris & t_tris)
    return overlap / len(q_tris)


def _document_row(document_id: str, chunks: list[Chunk], source_id: str) -> dict[str, Any]:
    """In-memory equivalent of the SQL aggregate in ``list_source_documents_async``."""
    meta = [dict(c.metadata or {}) for c in chunks]

    def _first(key: str) -> str:
        return next((str(m[key]) for m in meta if m.get(key)), "")

    scores = [
        float(m["quality_score"]) for m in meta if isinstance(m.get("quality_score"), int | float)
    ]
    return {
        "id": document_id,
        "source_id": source_id,
        "doc_id": document_id,
        "title": _first("doc_title"),
        "source_url": _first("source_url"),
        "content_hash": _first("doc_content_hash"),
        "language": _first("language"),
        "chunk_count": len(chunks),
        "quality_score": max(scores) if scores else None,
        "has_pii_redacted": any(m.get("has_pii_redacted") in (True, "true") for m in meta),
        "ingested_at": None,
        "expires_at": None,
    }


# D1: the "source" of an uploaded file. A same-name re-upload replaces only a
# document of the same source: uploads match uploads, a connector document matches
# only documents of its own ``source_id``. Recorded on upload chunks as
# ``metadata.ingest_source`` (never as ``source_id``, which names a real Source).
UPLOAD_SOURCE = "upload"
# Same-name matches considered per replace (a file name is one document per source;
# more than this many copies only exist after the pre-P1a random-id bug).
_SAME_NAME_MATCH_CAP = 1000


def _is_upload_metadata(meta: dict[str, Any]) -> bool:
    """True when a chunk was stored by the file-upload path.

    New uploads say so (``ingest_source = "upload"``). Uploads stored before D1
    carry no marker; they are the only chunks with an ``ext`` key and neither a
    Source (``source_id``), a page (``source_url``) nor a repository (``repo_url``).
    Kept in sync with the SQL in :meth:`KnowledgeStore.same_name_document_ids_async`.
    """
    if str(meta.get("source_id") or ""):
        return False
    marker = meta.get("ingest_source")
    if marker is not None:
        return str(marker) == UPLOAD_SOURCE
    return (
        "ext" in meta
        and not str(meta.get("source_url") or "")
        and not str(meta.get("repo_url") or "")
    )


# Chunk metadata keys a document listing's ``search`` matches (title / file / URL).
_DOCUMENT_SEARCH_KEYS = ("doc_title", "title", "source_file", "filename", "source_url")
# Documents counted exactly for a listing's ``total``; past it ``total_capped``.
_DOCUMENT_COUNT_CAP = 10_000


def _escape_like(term: str) -> str:
    """Escape ILIKE wildcards so a search term matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _document_listing_row(
    *,
    document_id: str,
    chunk_count: int,
    created_at: str | None,
    expires_at: str | None,
    title: str,
    source_url: str,
    source_type: str,
    source_file: str,
    preview: str,
) -> dict[str, Any]:
    return {
        "id": document_id,
        "document_id": document_id,
        "title": title or source_url or source_file or document_id,
        "source": source_url or source_file,
        "source_url": source_url,
        "source_type": source_type or "unknown",
        "chunk_count": chunk_count,
        "created_at": created_at,
        "expires_at": expires_at,
        "preview": preview,
    }


def _document_page(documents: list[dict[str, Any]], *, limit: int, counted: int) -> dict[str, Any]:
    capped = counted > _DOCUMENT_COUNT_CAP
    return {
        "documents": documents,
        "total": _DOCUMENT_COUNT_CAP if capped else counted,
        "total_capped": capped,
        "next_cursor": documents[-1]["id"] if len(documents) == limit and documents else None,
        "limit": limit,
    }


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

    def __init__(
        self,
        db_session_factory: Any = None,
        *,
        embedding_dim: int | None = None,
        embedder_name: str | None = None,
    ) -> None:
        # Key: (tenant_id, collection_id) → _CollectionStore
        self._data: dict[tuple[str, str], _CollectionStore] = {}
        self._index_records: dict[tuple[str, str], list[RAGIndexRecord]] = {}
        # No-DB (dev/test) staging of reconciliation listings, by (tenant, run).
        self._live_listings: dict[tuple[str, str], set[str]] = {}
        self._db = db_session_factory
        # Called with the tenant id after the tenant's knowledge changed (chunks
        # written or deleted) — e.g. SemanticCache.invalidate_tenant, so cached
        # answers built from the old knowledge stop being served.
        self._change_listeners: list[Any] = []
        # The active embedder's REAL output width, when known. New collections are
        # sized to it instead of the static settings.embedding_dim.
        self._embedding_dim = embedding_dim
        # The active embedder's model name (USR-3): what new collections are
        # labelled with instead of a hardcoded "voyage".
        self._embedder_name = (embedder_name or "").strip() or None

    def add_change_listener(self, listener: Any) -> None:
        """Register ``async listener(tenant_id)`` for knowledge changes."""
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    async def _notify_changed(self, tenant_id: str) -> None:
        """Best-effort: a listener failure never fails the write that committed."""
        for listener in list(self._change_listeners):
            try:
                await listener(tenant_id)
            except Exception as exc:
                _log.warning("knowledge_change_listener_failed: %s", exc)

    def set_embedding_dim(self, dimension: int | None) -> None:
        """Bind the active embedder's real output dimension (None = unknown)."""
        self._embedding_dim = dimension

    def set_embedder_name(self, name: str | None) -> None:
        """Bind the active embedder's model name (None = unknown)."""
        self._embedder_name = (name or "").strip() or None

    @property
    def embedder_name(self) -> str | None:
        """The active embedder's model name, when known."""
        return self._embedder_name

    def _label_new_collection(self, collection: KnowledgeCollection) -> None:
        """Name the embedder a new collection's vectors will come from (USR-3)."""
        if not (collection.embedder or "").strip():
            collection.embedder = self._embedder_name or _UNKNOWN_EMBEDDER

    def create_collection(
        self, collection: KnowledgeCollection, *, tenant_ctx: TenantContext
    ) -> str:
        """Create a collection in the explicit in-memory development store."""
        if self._db is not None:
            raise RuntimeError("Use create_collection_async for a persisted KnowledgeStore")
        from app.tenancy.limits import check_knowledge_collection_limit

        key = (tenant_ctx.tenant_id, collection.collection_id)
        if key not in self._data:  # RATE-01: the plan's collection limit
            current = sum(1 for tid, _ in self._data if tid == tenant_ctx.tenant_id)
            check_knowledge_collection_limit(tenant_ctx, current)
        self._label_new_collection(collection)
        if collection.embedding_dim is None:
            collection.embedding_dim = self._embedding_dim
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
        await self._db_create_collection(collection, tenant_ctx)
        self._data[(tenant_ctx.tenant_id, collection.collection_id)] = _CollectionStore(
            collection=collection
        )
        return collection.collection_id

    async def _db_create_collection(
        self, collection: KnowledgeCollection, tenant_ctx: TenantContext
    ) -> None:
        if self._db is None:
            return
        from sqlalchemy import text

        from app.core.config import get_settings
        from app.db.rls import sqlalchemy_rls_context
        from app.tenancy.limits import check_knowledge_collection_limit

        tenant_id = tenant_ctx.tenant_id
        self._label_new_collection(collection)

        # Size the collection to the ACTIVE embedder's real output width when it
        # is known (e.g. all-mpnet-base-v2 → 768 even with EMBEDDING_DIM=2048);
        # a known width with no chunk table is a clear error, not a collection
        # that can never be written. Unknown → the configured embedding_dim.
        # Existing collections are untouched (and an empty one still adopts the
        # real width on its first write, see _persist_chunks).
        if self._embedding_dim:
            dim = int(self._embedding_dim)
            _chunk_table(dim)  # raises EmbeddingDimensionError when unsupported
        else:
            dim = int(getattr(get_settings(), "embedding_dim", 768) or 768)
            if dim not in SUPPORTED_EMBEDDING_DIMENSIONS:
                dim = 768
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            # RATE-01: the plan's collection limit, counted and enforced in the
            # INSERT's transaction under a per-tenant advisory lock, so
            # concurrent creates on any replica serialise and cannot overshoot.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"knowledge_collections:{tenant_id}"},
            )
            current = (
                await session.execute(
                    text("SELECT count(*) FROM knowledge_collections WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            check_knowledge_collection_limit(tenant_ctx, int(current))
            created_id = (
                await session.execute(
                    text(
                        "INSERT INTO knowledge_collections "
                        "(id, tenant_id, name, description, embedder, embedding_dim) "
                        "SELECT :id, :tid, :name, :description, :embedder, :dim "
                        "FROM tenants WHERE id = :tid AND is_active IS TRUE "
                        "RETURNING id"
                    ),
                    {
                        "id": collection.collection_id,
                        "tid": tenant_id,
                        "name": collection.name,
                        "description": collection.description,
                        "embedder": collection.embedder,
                        "dim": dim,
                    },
                )
            ).scalar_one_or_none()
            if created_id is None:
                raise KeyError(f"Active tenant not found: {tenant_id}")
        collection.embedding_dim = dim

    def get_collection(
        self, collection_id: str, *, tenant_ctx: TenantContext
    ) -> KnowledgeCollection | None:
        store = self._data.get((tenant_ctx.tenant_id, collection_id))
        return store.collection if store is not None else None

    def list_collections(self, *, tenant_ctx: TenantContext) -> list[KnowledgeCollection]:
        return [v.collection for (tid, _), v in self._data.items() if tid == tenant_ctx.tenant_id]

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
                        "collection.document_count, collection.embedder, "
                        "collection.embedding_dim "
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
            embedder=str(row[4] or _UNKNOWN_EMBEDDER),
            embedding_dim=int(row[5]) if row[5] is not None else None,
        )

    async def get_collection_embedding_dim(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> int | None:
        """Return the collection's already-established embedding dimension.

        ``None`` means the collection has no persisted vectors yet, so any
        dimension is safe to write. Backs the D-10 dimension-safety guard in
        ``IngestionOrchestrator`` (never write a mismatched-dimension vector
        for the selected model — degrade to the default embedder instead).
        Reads the *same* ``embedding_dim``/``chunk_count`` source of truth
        ``_persist_chunks`` already enforces at the persistence boundary,
        rather than duplicating a separate dimension check.
        """
        if self._db is None:
            cache_key = (tenant_ctx.tenant_id, collection_id)
            index_records = self._index_records.get(cache_key)
            if index_records:
                return index_records[0].embedding_dimension
            collection_store = self._data.get(cache_key)
            if collection_store and collection_store.chunks:
                return len(collection_store.chunks[0].embedding)
            return None

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
                        "SELECT embedding_dim, chunk_count FROM knowledge_collections "
                        "WHERE id = :id AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"id": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
            if row is None or int(row[1]) == 0:
                return None
            return int(row[0])

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
                        "collection.document_count, collection.embedder, "
                        "collection.embedding_dim "
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
                embedder=str(row[4] or _UNKNOWN_EMBEDDER),
                embedding_dim=int(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]

    async def collection_counters_async(
        self,
        *,
        tenant_ctx: TenantContext,
        collection_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Per-collection counters straight from the source of truth.

        DB path: one indexed read of ``knowledge_collections`` — whose
        ``document_count`` / ``chunk_count`` / ``total_size_bytes`` /
        ``last_indexed_at`` are maintained exactly by ``_persist_chunks`` under
        the collection row lock — so this is O(collections), never a scan of
        the (millions-row) chunk tables. Errors propagate (callers answer 5xx).
        """
        if self._db is None:
            out: list[dict[str, Any]] = []
            for (tid, cid), cstore in self._data.items():
                if tid != tenant_ctx.tenant_id or (collection_id and cid != collection_id):
                    continue
                chunks = cstore.chunks
                out.append(
                    {
                        "collection_id": cid,
                        "name": cstore.collection.name,
                        "embedder": cstore.collection.embedder,
                        "document_count": len({c.document_id for c in chunks}),
                        "chunk_count": len(chunks),
                        "total_size_bytes": sum(len(c.content.encode()) for c in chunks),
                        "embedding_dim": len(chunks[0].embedding) if chunks else None,
                        "last_indexed_at": None,
                    }
                )
            return out

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        sql = (
            "SELECT id, name, embedder, document_count, chunk_count, "
            "COALESCE(total_size_bytes, 0), embedding_dim, last_indexed_at "
            "FROM knowledge_collections "
            "WHERE tenant_id = :tid AND is_active IS TRUE"
        )
        params: dict[str, Any] = {"tid": tenant_ctx.tenant_id}
        if collection_id:
            sql += " AND id = :cid"
            params["cid"] = collection_id
        sql += " ORDER BY created_at"
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            rows = (await session.execute(text(sql), params)).fetchall()
        return [
            {
                "collection_id": str(r[0]),
                "name": str(r[1]),
                "embedder": str(r[2] or ""),
                "document_count": int(r[3] or 0),
                "chunk_count": int(r[4] or 0),
                "total_size_bytes": int(r[5] or 0),
                "embedding_dim": int(r[6]) if r[6] is not None and int(r[4] or 0) else None,
                "last_indexed_at": r[7].isoformat() if r[7] is not None else None,
            }
            for r in rows
        ]

    async def source_type_sample_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
        embedding_dim: int | None,
        sample_size: int = 5_000,
    ) -> tuple[dict[str, int], int]:
        """``source_type`` distribution over a BOUNDED sample of a collection's
        chunks (at most ``sample_size`` rows via the (tenant_id, collection_id)
        index) — exact for small collections, a sample for huge ones, never a
        full scan. Returns (distribution, rows_sampled)."""
        if self._db is None:
            cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
            dist: dict[str, int] = {}
            chunks = cstore.chunks[:sample_size] if cstore is not None else []
            for c in chunks:
                key = str((c.metadata or {}).get("source_type") or "unknown")
                dist[key] = dist.get(key, 0) + 1
            return dist, len(chunks)
        if not embedding_dim:
            return {}, 0
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        table = _chunk_table(embedding_dim)
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT COALESCE(s.st, 'unknown'), COUNT(*) FROM ("
                        f"  SELECT metadata->>'source_type' AS st FROM {table} "
                        "  WHERE tenant_id = :tid AND collection_id = :cid LIMIT :n"
                        ") AS s GROUP BY 1"
                    ),
                    {"tid": tenant_ctx.tenant_id, "cid": collection_id, "n": sample_size},
                )
            ).fetchall()
        dist = {str(r[0]): int(r[1]) for r in rows}
        return dist, sum(dist.values())

    async def same_name_document_ids_async(
        self,
        *,
        tenant_ctx: TenantContext,
        collection_id: str,
        name: str,
        source: str,
    ) -> list[str]:
        """Documents of ``source`` in one collection stored under file name ``name``.

        D1: what a same-name re-upload may replace. ``source`` is
        :data:`UPLOAD_SOURCE` for uploads, else a connector's ``source_id``; a
        document of another source with the same name is never returned, and a
        document any of whose chunks belongs to another source is not either
        (fail closed). ``name`` matches ``source_file`` or ``doc_title`` exactly.
        Bounded (``_SAME_NAME_MATCH_CAP``) and served by the
        ``idx_knowledge_chunks_<dim>_source_file`` / ``_doc_title`` expression
        indexes, so it never scans a large collection. Raises ``KeyError`` for an
        unknown collection.
        """
        name = name.strip()
        if not name or not source:
            return []
        if self._db is None:
            cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
            if cstore is None:
                raise KeyError(f"Collection {collection_id} not found")
            named: set[str] = set()
            foreign: set[str] = set()
            for c in cstore.chunks:
                meta = c.metadata or {}
                if name not in (str(meta.get("source_file") or ""),
                                str(meta.get("doc_title") or "")):
                    continue
                named.add(c.document_id)
                if source == UPLOAD_SOURCE:
                    ours = _is_upload_metadata(meta)
                else:
                    ours = str(meta.get("source_id") or "") == source
                if not ours:
                    foreign.add(c.document_id)
            return sorted(named - foreign)[:_SAME_NAME_MATCH_CAP]

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        params: dict[str, Any] = {
            "tid": tenant_ctx.tenant_id,
            "cid": collection_id,
            "name": name,
            "cap": _SAME_NAME_MATCH_CAP,
        }
        if source == UPLOAD_SOURCE:
            # Mirrors _is_upload_metadata.
            params["upload"] = UPLOAD_SOURCE
            ours = (
                "(COALESCE(metadata->>'source_id', '') = '' AND ("
                "metadata->>'ingest_source' = :upload OR ("
                "NOT (metadata ? 'ingest_source') AND metadata ? 'ext' "
                "AND COALESCE(metadata->>'source_url', '') = '' "
                "AND COALESCE(metadata->>'repo_url', '') = '')))"
            )
        else:
            params["source"] = source
            ours = "(metadata->>'source_id' = :source)"
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dim = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).scalar_one_or_none()
            if dim is None:
                raise KeyError(f"Collection {collection_id} not found")
            table = _chunk_table(int(dim))
            rows = (
                await session.execute(
                    text(f"""
                        SELECT document_id FROM {table}
                        WHERE tenant_id = :tid AND collection_id = :cid
                          AND (metadata->>'source_file' = :name
                               OR metadata->>'doc_title' = :name)
                        GROUP BY document_id
                        HAVING bool_and({ours})
                        ORDER BY document_id
                        LIMIT :cap
                    """),
                    params,
                )
            ).fetchall()
        return [str(r[0]) for r in rows]

    async def list_source_documents_async(
        self,
        *,
        tenant_ctx: TenantContext,
        collection_id: str,
        source_id: str,
        limit: int = 50,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        """One row per indexed document of an ingestion Source (keyset-paginated
        by ``document_id``), aggregated in SQL from the chunk table.

        Served by ``idx_knowledge_chunks_<dim>_source_doc`` on
        (tenant_id, collection_id, metadata->>'source_id', document_id), so the
        GROUP BY streams in index order and stops at ``limit`` documents.
        """
        if self._db is None:
            cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
            grouped: dict[str, list[Chunk]] = {}
            for c in cstore.chunks if cstore is not None else []:
                if str((c.metadata or {}).get("source_id") or "") == source_id:
                    grouped.setdefault(c.document_id, []).append(c)
            ids = sorted(d for d in grouped if after is None or d > after)[:limit]
            return [_document_row(d, grouped[d], source_id) for d in ids]

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            # The collection's declared dimension (not its chunk counter, which a
            # drifted count could zero while chunks exist).
            table = await self._collection_chunk_table(session, collection_id, tenant_ctx)
            if table is None:
                return []
            rows = (
                await session.execute(
                    text(f"""
                        SELECT document_id,
                               COUNT(*),
                               MIN(created_at),
                               MAX(expires_at),
                               MAX(metadata->>'doc_title'),
                               MAX(metadata->>'source_url'),
                               MAX(metadata->>'doc_content_hash'),
                               MAX(metadata->>'language'),
                               NULL,
                               BOOL_OR(metadata->>'has_pii_redacted' = 'true'),
                               MAX(CASE WHEN jsonb_typeof(metadata->'quality_score') = 'number'
                                        THEN (metadata->>'quality_score')::float END)
                        FROM {table}
                        WHERE tenant_id = :tid AND collection_id = :cid
                          AND metadata->>'source_id' = :sid
                          AND (CAST(:after AS text) IS NULL OR document_id > CAST(:after AS text))
                        GROUP BY document_id
                        ORDER BY document_id
                        LIMIT :lim
                    """),
                    {
                        "tid": tenant_ctx.tenant_id,
                        "cid": collection_id,
                        "sid": source_id,
                        "after": after,
                        "lim": limit,
                    },
                )
            ).fetchall()
        return [
            {
                "id": str(r[0]),
                "source_id": source_id,
                "doc_id": str(r[0]),
                "title": str(r[4] or ""),
                "source_url": str(r[5] or ""),
                "content_hash": str(r[6] or ""),
                "language": str(r[7] or ""),
                "chunk_count": int(r[1] or 0),
                "quality_score": float(r[10]) if r[10] is not None else None,
                "has_pii_redacted": bool(r[9]),
                "ingested_at": r[2].isoformat() if r[2] is not None else None,
                "expires_at": r[3].isoformat() if r[3] is not None else None,
            }
            for r in rows
        ]

    async def list_collection_documents_async(
        self,
        *,
        tenant_ctx: TenantContext,
        collection_id: str,
        limit: int = 20,
        cursor: str | None = None,
        offset: int = 0,
        search: str | None = None,
        source_type: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Page through a collection's documents, aggregated from its chunk rows.

        A document has no row of its own: ``knowledge_documents`` only holds
        durable ingestion jobs, the indexed content lives in
        ``knowledge_chunks_<dim>`` keyed by ``document_id``. Pages are
        keyset-paginated on ``document_id`` (``cursor`` = last id of the previous
        page): the page's ids come from a DISTINCT scan of the
        ``(collection_id, document_id, chunk_index)`` unique index that stops
        after ``limit`` documents, then only those documents are aggregated. So a
        page costs the same in a million-chunk collection as in a small one.
        ``offset`` is kept for old clients and bounded by the API. ``total`` is
        an exact count up to ``_DOCUMENT_COUNT_CAP`` documents
        (``total_capped`` past it), never an unbounded COUNT(DISTINCT).
        """
        search_term = (search or "").strip()
        source_filter = (source_type or "").strip()
        if self._db is None:
            return self._list_collection_documents_memory(
                tenant_ctx=tenant_ctx,
                collection_id=collection_id,
                limit=limit,
                cursor=cursor,
                offset=offset,
                search=search_term,
                source_type=source_filter,
                document_id=document_id,
            )

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dim = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).scalar_one_or_none()
            if dim is None:
                raise KeyError(f"Collection {collection_id} not found")
            table = _chunk_table(int(dim))
            filters = ""
            params: dict[str, Any] = {"tid": tenant_ctx.tenant_id, "cid": collection_id}
            if source_filter:
                filters += " AND metadata->>'source_type' = :stype"
                params["stype"] = source_filter
            if document_id:
                filters += " AND document_id = :did"
                params["did"] = document_id
            if search_term:
                filters += " AND (" + " OR ".join(
                    f"COALESCE(metadata->>'{key}', '') ILIKE :q ESCAPE '\\'"
                    for key in _DOCUMENT_SEARCH_KEYS
                ) + ")"
                params["q"] = "%" + _escape_like(search_term) + "%"
            scope = f"FROM {table} WHERE tenant_id = :tid AND collection_id = :cid{filters}"

            page_ids = [
                str(r[0])
                for r in (
                    await session.execute(
                        text(
                            f"SELECT DISTINCT document_id {scope} "
                            "AND (CAST(:after AS text) IS NULL "
                            "OR document_id > CAST(:after AS text)) "
                            "ORDER BY document_id LIMIT :lim OFFSET :off"
                        ),
                        {**params, "after": cursor or None, "lim": limit, "off": offset},
                    )
                ).fetchall()
            ]
            counted = int(
                (
                    await session.execute(
                        text(
                            f"SELECT COUNT(*) FROM (SELECT DISTINCT document_id {scope} "
                            "LIMIT :cap) AS docs"
                        ),
                        {**params, "cap": _DOCUMENT_COUNT_CAP + 1},
                    )
                ).scalar_one()
                or 0
            )
            rows = []
            if page_ids:
                rows = (
                    await session.execute(
                        text(f"""
                            SELECT d.document_id, d.chunk_count, d.created_at, d.expires_at,
                                   d.title, d.source_url, d.source_type, d.source_file,
                                   p.preview
                            FROM (
                                SELECT document_id,
                                       COUNT(*) AS chunk_count,
                                       MIN(created_at) AS created_at,
                                       MAX(expires_at) AS expires_at,
                                       MAX(COALESCE(NULLIF(metadata->>'doc_title', ''),
                                                    NULLIF(metadata->>'title', ''),
                                                    NULLIF(metadata->>'source_file', ''),
                                                    NULLIF(metadata->>'filename', '')))
                                           AS title,
                                       MAX(NULLIF(metadata->>'source_url', '')) AS source_url,
                                       MAX(NULLIF(metadata->>'source_type', '')) AS source_type,
                                       MAX(NULLIF(metadata->>'source_file', '')) AS source_file
                                FROM {table}
                                WHERE tenant_id = :tid AND collection_id = :cid
                                  AND document_id = ANY(CAST(:ids AS text[]))
                                GROUP BY document_id
                            ) AS d
                            LEFT JOIN LATERAL (
                                SELECT LEFT(c.content, 200) AS preview
                                FROM {table} AS c
                                WHERE c.tenant_id = :tid AND c.collection_id = :cid
                                  AND c.document_id = d.document_id
                                ORDER BY c.chunk_index
                                LIMIT 1
                            ) AS p ON TRUE
                            ORDER BY d.document_id
                        """),
                        {"tid": tenant_ctx.tenant_id, "cid": collection_id, "ids": page_ids},
                    )
                ).fetchall()

        documents = [
            _document_listing_row(
                document_id=str(r[0]),
                chunk_count=int(r[1] or 0),
                created_at=r[2].isoformat() if r[2] is not None else None,
                expires_at=r[3].isoformat() if r[3] is not None else None,
                title=str(r[4] or ""),
                source_url=str(r[5] or ""),
                source_type=str(r[6] or ""),
                source_file=str(r[7] or ""),
                preview=str(r[8] or ""),
            )
            for r in rows
        ]
        return _document_page(documents, limit=limit, counted=counted)

    def _list_collection_documents_memory(
        self,
        *,
        tenant_ctx: TenantContext,
        collection_id: str,
        limit: int,
        cursor: str | None,
        offset: int,
        search: str,
        source_type: str,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
        if cstore is None:
            raise KeyError(f"Collection {collection_id} not found")
        needle = search.casefold()
        grouped: dict[str, list[Chunk]] = {}
        for c in cstore.chunks:
            meta = c.metadata or {}
            if document_id and c.document_id != document_id:
                continue
            if source_type and str(meta.get("source_type") or "") != source_type:
                continue
            if needle and not any(
                needle in str(meta.get(key) or "").casefold() for key in _DOCUMENT_SEARCH_KEYS
            ):
                continue
            grouped.setdefault(c.document_id, []).append(c)
        ids = sorted(d for d in grouped if cursor is None or d > cursor)
        documents = []
        for doc_id in ids[offset : offset + limit]:
            chunks = sorted(grouped[doc_id], key=lambda c: c.chunk_index)
            meta = [dict(c.metadata or {}) for c in chunks]

            def _first(*keys: str, _meta: list[dict[str, Any]] = meta) -> str:
                for key in keys:
                    for m in _meta:
                        if m.get(key):
                            return str(m[key])
                return ""

            documents.append(
                _document_listing_row(
                    document_id=doc_id,
                    chunk_count=len(chunks),
                    created_at=None,
                    expires_at=None,
                    title=_first("doc_title", "title", "source_file", "filename"),
                    source_url=_first("source_url"),
                    source_type=_first("source_type"),
                    source_file=_first("source_file"),
                    preview=chunks[0].content[:200],
                )
            )
        return _document_page(documents, limit=limit, counted=min(len(grouped),
                                                                  _DOCUMENT_COUNT_CAP + 1))

    # ── Upstream-deletion reconciliation (KB-44) ─────────────────────────────
    # The upstream listing is staged in ``ingestion_live_listings`` (one run id
    # per run) in bounded batches; Postgres then finds the Source's indexed
    # documents that are NOT listed. Process memory stays bounded at any bucket
    # size. Without a database the staging lives in this (dev-only) store.

    async def begin_live_listing_async(
        self, run_id: str, *, tenant_ctx: TenantContext
    ) -> datetime:
        """Start reconciliation run ``run_id``; returns the database clock's now.

        Only documents indexed before this instant are deletion candidates: a
        document a concurrent sync indexes while the run is listing is absent
        from the listing because it is NEW, not gone. Also purges staged rows a
        crashed run of this tenant left behind (older than a day).
        """
        if self._db is None:
            return datetime.now(UTC)
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await session.execute(
                text(
                    "DELETE FROM ingestion_live_listings WHERE tenant_id = :tid "
                    "AND created_at < now() - interval '1 day'"
                ),
                {"tid": tenant_ctx.tenant_id},
            )
            started = (await session.execute(text("SELECT now()"))).scalar_one()
        return cast(datetime, started)

    async def stage_live_doc_ids_async(
        self, run_id: str, doc_ids: list[str], *, tenant_ctx: TenantContext
    ) -> None:
        """Stage one batch of upstream ids for reconciliation run ``run_id``."""
        if not doc_ids:
            return
        if self._db is None:
            self._live_listings.setdefault((tenant_ctx.tenant_id, run_id), set()).update(
                doc_ids
            )
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO ingestion_live_listings (tenant_id, run_id, doc_id) "
                    "SELECT :tid, :run, unnest(CAST(:ids AS text[])) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"tid": tenant_ctx.tenant_id, "run": run_id, "ids": list(doc_ids)},
            )

    async def list_unlisted_source_documents_async(
        self,
        run_id: str,
        *,
        tenant_ctx: TenantContext,
        collection_id: str,
        source_id: str,
        indexed_before: datetime,
        after: str | None = None,
        limit: int = 1000,
    ) -> list[str]:
        """The Source's indexed document ids NOT staged under ``run_id`` (keyset page).

        Walks ``idx_knowledge_chunks_<dim>_source_doc`` in ``document_id`` order
        once across all pages, probing the staged listing's primary key per
        document. A document with any chunk written at or after
        ``indexed_before`` (re-indexed or new since the run began) is never
        returned. The no-database store keeps no chunk timestamps (dev only).
        """
        if self._db is None:
            listed = self._live_listings.get((tenant_ctx.tenant_id, run_id), set())
            cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
            ids = {
                c.document_id
                for c in (cstore.chunks if cstore is not None else [])
                if str((c.metadata or {}).get("source_id") or "") == source_id
                and c.document_id not in listed
                and (after is None or c.document_id > after)
            }
            return sorted(ids)[:limit]
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            # The collection's declared dimension (not its chunk counter, which a
            # drifted count could zero while chunks exist).
            table = await self._collection_chunk_table(session, collection_id, tenant_ctx)
            if table is None:
                return []
            rows = (
                await session.execute(
                    text(
                        f"SELECT c.document_id FROM {table} c "
                        "WHERE c.tenant_id = :tid AND c.collection_id = :cid "
                        "AND c.metadata->>'source_id' = :sid "
                        "AND (CAST(:after AS text) IS NULL "
                        "     OR c.document_id > CAST(:after AS text)) "
                        "AND NOT EXISTS (SELECT 1 FROM ingestion_live_listings l "
                        "  WHERE l.tenant_id = :tid AND l.run_id = :run "
                        "  AND l.doc_id = c.document_id) "
                        "GROUP BY c.document_id "
                        "HAVING max(c.created_at) < :before "
                        "ORDER BY c.document_id LIMIT :lim"
                    ),
                    {
                        "tid": tenant_ctx.tenant_id,
                        "cid": collection_id,
                        "sid": source_id,
                        "after": after,
                        "run": run_id,
                        "before": indexed_before,
                        "lim": limit,
                    },
                )
            ).fetchall()
        return [str(r[0]) for r in rows]

    async def clear_live_listing_async(self, run_id: str, *, tenant_ctx: TenantContext) -> None:
        """Drop run ``run_id``'s staged listing."""
        if self._db is None:
            self._live_listings.pop((tenant_ctx.tenant_id, run_id), None)
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await session.execute(
                text(
                    "DELETE FROM ingestion_live_listings "
                    "WHERE tenant_id = :tid AND run_id = :run"
                ),
                {"tid": tenant_ctx.tenant_id, "run": run_id},
            )

    async def held_document_ids_async(
        self, collection_id: str, document_ids: list[str], *, tenant_ctx: TenantContext
    ) -> set[str]:
        """The subset of ``document_ids`` (of ``collection_id``) under an in-force hold.

        A tenant-wide or collection hold covers every id. One query per call
        (callers pass a page of ids), never one per document. Errors propagate:
        the caller must not delete what it could not check. Without a database
        there are no durable holds.
        """
        if self._db is None or not document_ids:
            return set()
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
                        "SELECT d.rid FROM unnest(CAST(:ids AS text[])) AS d(rid) "
                        "WHERE EXISTS (SELECT 1 FROM legal_holds lh "
                        "WHERE lh.tenant_id = :tid AND lh.status = 'active' "
                        "AND (lh.expires_at IS NULL OR lh.expires_at > now()) "
                        "AND (lh.resource_type = 'tenant' "
                        "OR lh.resource_ids @> jsonb_build_array(CAST(:cid AS text)) "
                        "OR lh.resource_ids @> jsonb_build_array(d.rid)))"
                    ),
                    {
                        "ids": list(document_ids),
                        "tid": tenant_ctx.tenant_id,
                        "cid": collection_id,
                    },
                )
            ).fetchall()
        return {str(r[0]) for r in rows}

    async def existing_document_ids_async(
        self, collection_id: str, document_ids: list[str], *, tenant_ctx: TenantContext
    ) -> set[str]:
        """The subset of ``document_ids`` that has chunks in ``collection_id``.

        One indexed probe per call (callers pass a bounded batch), served by the
        ``(collection_id, document_id, chunk_index)`` unique index. Errors
        propagate. An unknown collection holds nothing.
        """
        if not document_ids:
            return set()
        wanted = set(document_ids)
        if self._db is None:
            cstore = self._data.get((tenant_ctx.tenant_id, collection_id))
            return {
                c.document_id
                for c in (cstore.chunks if cstore is not None else [])
                if c.document_id in wanted
            }
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            table = await self._collection_chunk_table(session, collection_id, tenant_ctx)
            if table is None:
                return set()
            rows = (
                await session.execute(
                    text(
                        "SELECT d.did FROM unnest(CAST(:ids AS text[])) AS d(did) "
                        f"WHERE EXISTS (SELECT 1 FROM {table} c WHERE c.collection_id = :cid "
                        "AND c.tenant_id = :tid AND c.document_id = d.did)"
                    ),
                    {"ids": sorted(wanted), "cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchall()
        return {str(r[0]) for r in rows}

    async def collection_under_legal_hold_async(
        self, collection_id: str, *, tenant_ctx: TenantContext
    ) -> bool:
        """True when an in-force hold covers the tenant, the collection or any of its documents.

        Errors propagate: the caller must refuse the deletion when this cannot
        be answered. Without a database there are no durable holds.
        """
        if self._db is None:
            return False
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            table = await self._collection_chunk_table(session, collection_id, tenant_ctx)
            row = (
                await session.execute(
                    text(_collection_held_sql(table)),
                    {"tid": tenant_ctx.tenant_id, "cid": collection_id},
                )
            ).fetchone()
        return row is not None

    async def _collection_chunk_table(
        self, session: Any, collection_id: str, tenant_ctx: TenantContext
    ) -> str | None:
        from sqlalchemy import text

        dimension_row = (
            await session.execute(
                text(
                    "SELECT embedding_dim FROM knowledge_collections "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": collection_id, "tenant_id": tenant_ctx.tenant_id},
            )
        ).fetchone()
        if dimension_row is None or dimension_row[0] is None:
            return None
        dimension = int(dimension_row[0])
        if dimension not in SUPPORTED_EMBEDDING_DIMENSIONS:
            return None
        return _chunk_table(dimension)

    async def delete_collection_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> bool:
        """Delete one owned collection, its chunks and the graph extracted from them.

        Chunks go in bounded batches (one short transaction each), each batch
        taking the knowledge-graph rows stamped with its chunks along — the
        graph has no FK to collections, so a cascade never reached it and
        deleted documents' entities kept surfacing in GraphRAG. The collection
        row goes last; its cascade then only sweeps chunks a concurrent ingest
        added meanwhile.
        """
        key = (tenant_ctx.tenant_id, collection_id)
        if self._db is None:
            removed_collection = self._data.pop(key, None) is not None
            if removed_collection:
                await self._notify_changed(tenant_ctx.tenant_id)
            return removed_collection
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.rag.retention import delete_document_graph

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            table = await self._collection_chunk_table(session, collection_id, tenant_ctx)
        held_sql = text(_collection_held_sql(table))
        held_params = {"tid": tenant_ctx.tenant_id, "cid": collection_id}

        async def _refuse_if_held(session: Any) -> None:
            # Re-checked inside every batch's transaction: a hold placed while a
            # large collection is being deleted stops the rest of the delete.
            if (await session.execute(held_sql, held_params)).fetchone() is not None:
                raise KnowledgeLegalHoldError(
                    f"collection {collection_id} or one of its documents is under legal hold"
                )

        # An unknown width has no chunk table to batch through; the collection
        # row's cascade below is then the only cleanup there is.
        if table is not None:
            while True:
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    await _refuse_if_held(session)
                    removed = (
                        await session.execute(
                            text(
                                f"DELETE FROM {table} WHERE id IN ("
                                f"SELECT id FROM {table} WHERE collection_id = :cid "
                                "AND tenant_id = :tid LIMIT :lim) "
                                "RETURNING document_id, octet_length(content), chunk_index, id"
                            ),
                            {
                                "cid": collection_id,
                                "tid": tenant_ctx.tenant_id,
                                "lim": _COLLECTION_DELETE_BATCH,
                            },
                        )
                    ).fetchall()
                    by_document: dict[str, list[Any]] = {}
                    for row in removed:
                        by_document.setdefault(str(row[0]), []).append(tuple(row[1:]))
                    for document_id, rows in by_document.items():
                        await delete_document_graph(
                            session, tenant_ctx.tenant_id, document_id, rows
                        )
                if len(removed) < _COLLECTION_DELETE_BATCH:
                    break
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await _refuse_if_held(session)
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
        await self._notify_changed(tenant_ctx.tenant_id)
        return True

    async def count_active_ingestion_jobs_async(
        self, *, tenant_ctx: TenantContext, source_type: str = "repository"
    ) -> int:
        """The tenant's queued or running durable ingestion jobs of ``source_type``."""
        if self._db is None:
            return 0
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            count = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM knowledge_documents "
                        "WHERE tenant_id = :tenant_id AND source_type = :source_type "
                        "AND domain_metadata->>'record_type' = 'ingestion_job' "
                        "AND status IN ('queued', 'running')"
                    ),
                    {"tenant_id": tenant_ctx.tenant_id, "source_type": source_type},
                )
            ).scalar_one()
        return int(count or 0)

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

    async def exists_by_hash(
        self,
        *,
        content_hash: str,
        tenant_id: str,
        collection_id: str | None = None,
        document_id: str | None = None,
    ) -> bool:
        """Return True if content with this hash is already indexed (WS-12 dedup).

        RLS/tenant-scoped content-hash lookup that backs cross-source dedup and
        incremental re-ingest. A hash matches when either a chunk's native
        per-chunk ``content_hash`` OR its ``doc_content_hash`` metadata equals
        ``content_hash``. Ingestion (pipeline Stage 3), RPA→KB and OCR→KB all
        call this before re-indexing, so the SAME content from any source dedups
        against the one store.

        Args:
            content_hash: SHA-256 hex digest to look up. Empty never matches.
            tenant_id: tenant whose collections are searched (RLS-scoped).
            collection_id: when given, restrict the search to that collection;
                otherwise search every collection in the tenant.
            document_id: when given, only that document counts — "is THIS
                document already indexed with this content" (a connector's
                unchanged item), not "does any document hold it" (P1b-6).
        """
        return (
            await self.document_id_by_hash(
                content_hash=content_hash,
                tenant_id=tenant_id,
                collection_id=collection_id,
                document_id=document_id,
            )
            is not None
        )

    async def document_id_by_hash(
        self,
        *,
        content_hash: str,
        tenant_id: str,
        collection_id: str | None = None,
        document_id: str | None = None,
    ) -> str | None:
        """The id of a document already holding this content (see :meth:`exists_by_hash`),
        or None. A deduplicated upload reports it, so the caller learns where the
        content lives instead of getting ``document_id: null``."""
        if not content_hash:
            return None
        if self._db is None:
            return self._document_id_by_hash_memory(
                content_hash, tenant_id, collection_id, document_id
            )
        return await self._db_document_id_by_hash(
            content_hash, tenant_id, collection_id, document_id
        )

    def _exists_by_hash_memory(
        self, content_hash: str, tenant_id: str, collection_id: str | None
    ) -> bool:
        return (
            self._document_id_by_hash_memory(content_hash, tenant_id, collection_id) is not None
        )

    def _document_id_by_hash_memory(
        self,
        content_hash: str,
        tenant_id: str,
        collection_id: str | None,
        document_id: str | None = None,
    ) -> str | None:
        for (tid, cid), store in self._data.items():
            if tid != tenant_id:
                continue
            if collection_id is not None and cid != collection_id:
                continue
            for chunk in store.chunks:
                if document_id is not None and chunk.document_id != document_id:
                    continue
                meta = chunk.metadata or {}
                if (
                    meta.get("doc_content_hash") == content_hash
                    or meta.get("content_hash") == content_hash
                ):
                    return chunk.document_id
        return None

    async def _db_document_id_by_hash(
        self,
        content_hash: str,
        tenant_id: str,
        collection_id: str | None,
        document_id: str | None = None,
    ) -> str | None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        if collection_id is not None:
            dim = await self.get_collection_embedding_dim(
                collection_id,
                tenant_ctx=TenantContext(
                    tenant_id=tenant_id, api_key_id="dedup", plan=PlanTier.FREE
                ),
            )
            dims: tuple[int, ...] = (dim,) if dim else SUPPORTED_EMBEDDING_DIMENSIONS
        else:
            dims = SUPPORTED_EMBEDDING_DIMENSIONS

        params: dict[str, Any] = {"tid": tenant_id, "h": content_hash}
        collection_clause = ""
        if collection_id is not None:
            collection_clause = "AND collection_id = :cid"
            params["cid"] = collection_id
        if document_id is not None:
            collection_clause += " AND document_id = :did"
            params["did"] = document_id

        for dimension in dims:
            table = _chunk_table(dimension)
            # Each dimension is checked in its own transaction so a table that
            # does not exist in this schema cannot abort the others.
            try:
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    found = (
                        await session.execute(
                            text(
                                f"SELECT document_id FROM {table} "
                                "WHERE tenant_id = :tid "
                                f"{collection_clause} "
                                "AND (content_hash = :h "
                                "OR metadata->>'doc_content_hash' = :h) "
                                "LIMIT 1"
                            ),
                            params,
                        )
                    ).scalar_one_or_none()
                if found is not None:
                    return str(found)
            except Exception as exc:  # missing table / transient DB error
                _log.debug("exists_by_hash_probe_error table=%s: %s", table, exc)
                continue
        return None

    async def _db_ingest_chunk(self, chunk: Chunk, collection_id: str, tenant_id: str) -> None:
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
                c
                for c in chunks_to_score
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
            _bm25_hits = {h.chunk_id: h.score for h in _bm25.search(query, top_k=len(scored))}
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
                query,
                query_embedding,
                collection_id,
                tenant_ctx,
                top_k,
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

    async def binary_prefilter_search(
        self,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
        *,
        shortlist: int = 200,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[HybridSearchResult]:
        """Two-stage vector search using the binary_quantize() Hamming index.

        Stage 1 (cheap, index-backed): shortlist ``shortlist`` chunks by Hamming
        distance over ``binary_quantize(embedding)::bit(dim)`` — 32x smaller codes,
        served by the migration-0120 HNSW ``bit_hamming_ops`` index. Stage 2:
        rerank that shortlist by full-precision cosine and return ``top_k``.

        This is the storage-layer counterpart to the in-process quantizer: the
        coarse filter runs in Postgres against the compact binary index, and only
        a small candidate set is scored at full precision. Requires pgvector >= 0.7
        and the 0120 index; callers opt in (``rag_binary_prefilter_enabled``).
        """
        if self._db is None or not query_embedding:
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
            dim = int(dimension_row[0])
            table = _chunk_table(dim)
            metadata_clause = (
                " AND metadata @> CAST(:metadata_filter AS jsonb)" if metadata_filter else ""
            )
            # Stage 1 Hamming shortlist (bit index) → Stage 2 exact cosine rerank.
            sql = text(
                f"""
                WITH shortlist AS (
                    SELECT id, content, metadata, embedding
                      FROM {table}
                     WHERE collection_id = :cid{metadata_clause}
                     ORDER BY binary_quantize(embedding)::bit({dim})
                              <~> binary_quantize(CAST(:emb AS vector))::bit({dim})
                     LIMIT :shortlist
                )
                SELECT id, content, metadata,
                       1 - (embedding <=> CAST(:emb AS vector)) AS score
                  FROM shortlist
                 ORDER BY embedding <=> CAST(:emb AS vector)
                 LIMIT :top_k
                """
            )
            params: dict[str, Any] = {
                "cid": collection_id,
                "emb": str(query_embedding),
                "shortlist": max(int(shortlist), top_k),
                "top_k": top_k,
            }
            if metadata_filter:
                import json as _json

                params["metadata_filter"] = _json.dumps(metadata_filter)
            rows = (await session.execute(sql, params)).fetchall()

        results: list[HybridSearchResult] = []
        for row in rows:
            meta = row[2] if isinstance(row[2], dict) else {}
            results.append(
                HybridSearchResult(
                    chunk_id=str(row[0]),
                    content=str(row[1]),
                    score=float(row[3]),
                    vector_score=float(row[3]),
                    trigram_score=0.0,
                    source_url=str(meta.get("source_url", "") or ""),
                    source_doc_id=str(meta.get("source_doc_id", "") or ""),
                    metadata=meta,
                )
            )
        return results

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
                    chunk.metadata.get(key) == value for key, value in metadata_filter.items()
                ):
                    continue
                tri = _trigram_score(query, chunk.content)
                results.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "content": chunk.content,
                        "score": tri,
                        "metadata": chunk.metadata,
                    }
                )
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    async def _resolve_collection_id(
        self, name_or_id: str, tenant_ctx: TenantContext
    ) -> str | None:
        """Resolve a collection reference that may be either an id or a name."""
        by_id = await self.get_collection_async(name_or_id, tenant_ctx=tenant_ctx)
        if by_id is not None:
            return by_id.collection_id
        for coll in await self.list_collections_async(tenant_ctx=tenant_ctx):
            if coll.name == name_or_id:
                return coll.collection_id
        return None

    async def retrieve(
        self,
        *,
        query: str,
        collection_name: str,
        top_k: int = 5,
        tenant_id: str,
        embedder: Any = None,
    ) -> list[dict[str, Any]]:
        """Retrieve knowledge chunks for a query — the entrypoint the workflow
        LLM/RAG steps call.

        ``collection_name`` may be a collection id or its human name. When an
        ``embedder`` (any ``LLMProvider`` with embedding support) is supplied the
        query is embedded for semantic hybrid retrieval (pgvector + FTS + trigram
        RRF fusion); without one it degrades to lexical search rather than
        failing. Returns plain dicts with a ``content`` key (plus ``score``,
        ``metadata``, ``chunk_id``), the shape the steps consume.
        """
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(tenant_id=tenant_id, api_key_id="workflow-rag", plan=PlanTier.FREE)
        collection_id = await self._resolve_collection_id(collection_name, ctx)
        if collection_id is None:
            return []

        query_embedding: list[float] = []
        if embedder is not None:
            try:
                from app.providers.base import embed_texts

                vectors = await embed_texts([query], provider=embedder)
                query_embedding = vectors[0] if vectors else []
            except Exception:
                query_embedding = []

        mode = "hybrid" if query_embedding else "lexical"
        hits = await self.hybrid_search_db(
            query,
            query_embedding,
            collection_id,
            ctx,
            top_k=top_k,
            retrieval_mode=mode,
        )
        return [
            {
                "content": hit.content,
                "score": hit.score,
                "metadata": hit.metadata,
                "chunk_id": hit.chunk_id,
            }
            for hit in hits
        ]

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
        store.chunks = [c for c in store.chunks if c.document_id != document_id]
        deleted = before - len(store.chunks)
        if deleted > 0:
            store.collection.document_count = len({c.document_id for c in store.chunks})
        return deleted

    async def get_document_source_async(
        self, document_id: str, *, collection_id: str, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Metadata of a document's first chunk (its source), or None if absent."""
        if self._db is None:
            store = self._data.get((tenant_ctx.tenant_id, collection_id))
            chunks = [c for c in (store.chunks if store else []) if c.document_id == document_id]
            if not chunks:
                return None
            return dict(min(chunks, key=lambda c: c.chunk_index).metadata or {})

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            dim = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).scalar_one_or_none()
            if dim is None:
                return None
            row = (
                await session.execute(
                    text(
                        f"SELECT metadata FROM {_chunk_table(int(dim))} "
                        "WHERE document_id = :did AND collection_id = :cid AND tenant_id = :tid "
                        "ORDER BY chunk_index LIMIT 1"
                    ),
                    {"did": document_id, "cid": collection_id, "tid": tenant_ctx.tenant_id},
                )
            ).first()
        if row is None:
            return None
        meta = row[0]
        return dict(json.loads(meta) if isinstance(meta, str) else (meta or {}))

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
            removed_chunks = self.delete_document(
                document_id,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
            )
            if removed_chunks:
                await self._notify_changed(tenant_ctx.tenant_id)
            return removed_chunks

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
            removed = (
                await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE document_id = :did "
                        "AND collection_id = :cid AND tenant_id = :tid "
                        "RETURNING octet_length(content), chunk_index, id"
                    ),
                    {
                        "did": document_id,
                        "cid": collection_id,
                        "tid": tenant_ctx.tenant_id,
                    },
                )
            ).fetchall()
            deleted = len(removed)
            if deleted:
                # The graph has no FK to the chunks it was extracted from: delete
                # the nodes stamped with these chunks (and their edges) with them,
                # or GraphRAG keeps surfacing the deleted document's entities.
                from app.rag.retention import delete_document_graph

                await delete_document_graph(
                    session, tenant_ctx.tenant_id, document_id, [tuple(r) for r in removed]
                )
                # Incremental, like the ingest path — the recompute this replaces
                # scanned the whole collection on every delete. It also never
                # touched total_size_bytes at all, so a collection's reported
                # size only ever grew: delete every document and the tenant was
                # still billed/quota'd for the bytes. One document, one row
                # lock, exact deltas.
                await session.execute(
                    text("""
                        UPDATE knowledge_collections
                        SET chunk_count = GREATEST(chunk_count - :d_chunks, 0),
                            document_count = GREATEST(document_count - 1, 0),
                            total_size_bytes = GREATEST(
                                COALESCE(total_size_bytes, 0) - :d_bytes, 0
                            ),
                            updated_at = now()
                        WHERE id = :cid AND tenant_id = :tid
                    """),
                    {
                        "cid": collection_id,
                        "tid": tenant_ctx.tenant_id,
                        "d_chunks": deleted,
                        "d_bytes": sum(int(r[0] or 0) for r in removed),
                    },
                )

        memory_deleted = self.delete_document(
            document_id,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        if deleted or memory_deleted:
            await self._notify_changed(tenant_ctx.tenant_id)
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
        merged_metadata.update(
            {
                "source_url": source_url,
                "source_type": source_type,
                "source_doc_id": document_id,
                "page_number": page_number,
                "freshness_ttl_hours": freshness_ttl_hours,
            }
        )

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

        # In-memory mirror only when there is no database. With a DB the chunk
        # rows are the source of truth for both search (hybrid_search_db) and the
        # collection counters, and nothing reads this mirror back — while
        # extending it kept every chunk this process ever ingested on the heap
        # (an unbounded per-replica leak at corpus scale) and recomputed
        # document_count with a set comprehension over the whole list, making a
        # bulk load O(n^2) in Python on top of the O(n^2) it was doing in SQL.
        if self._db is None:
            store = self._data.get((tenant_ctx.tenant_id, collection_id))
            if store is not None:
                store.chunks.append(chunk)
                store.collection.document_count = len(
                    {item.document_id for item in store.chunks}
                )

        await self._notify_changed(tenant_ctx.tenant_id)
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
        existing[:] = [record for record in existing if record.document_id != document_id]
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
        candidate_limit = min(top_k * 4, 100) if strategy.value == "agentic_chunking" else top_k
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
        candidate_limit = min(top_k * 4, 100) if strategy.value == "agentic_chunking" else top_k
        ranked = sorted(
            candidates,
            key=lambda record: (
                -(
                    _VECTOR_WEIGHT * _cosine_similarity(query_embedding, record.embedding)
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
            score = _VECTOR_WEIGHT * _cosine_similarity(
                query_embedding, record.embedding
            ) + _TRIGRAM_WEIGHT * _trigram_score(query, record.content)
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
        replace_document: bool = False,
        duplicates_within_document: bool = False,
        supersedes_document_id: str | None = None,
    ) -> list[str]:
        """Persist all chunks for one ingestion unit in a single transaction.

        ``duplicates_within_document`` (with ``replace_document``; connector
        syncs): only the SAME document holding this content is a duplicate. The
        same bytes under another upstream item (a backup copy) are that item's
        own document (P1b-6).

        ``replace_document`` (one document per call): the document's existing
        chunks are deleted in the same transaction, so a re-synced item with a
        stable id replaces its previous version instead of sitting beside it.
        The duplicate-content guard still applies — an unchanged document (same
        ``doc_content_hash``) raises :class:`DuplicateContentError` as before.

        ``supersedes_document_id``: another document this one takes over (D2 —
        a MongoDB document stored under its pre-v8 id). It is deleted (with its
        graph rows) in the same transaction BEFORE the duplicate-content guard,
        so the new id may carry the same content, and there is never a moment
        with neither copy nor one with both.
        """
        replacement_id: str | None = None
        if replace_document and chunks:
            document_ids = {chunk.document_id for chunk in chunks}
            if len(document_ids) != 1:
                raise ValueError("A document replacement must contain exactly one document")
            replacement_id = next(iter(document_ids))
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
            # Same TOCTOU guard as the DB path's ``_persist_chunks``: a caller's
            # earlier ``exists_by_hash`` dedup check ran in a prior ``await``
            # gap (parse/PII/chunk/embed), so a concurrent identical ingestion
            # can have slipped in in the meantime. This whole branch runs with
            # no ``await`` inside it, so re-checking here right before the
            # in-memory append is race-free for this store instance.
            doc_hashes = {
                str(chunk.metadata.get("doc_content_hash") or "") for chunk in chunks
            }
            doc_hashes.discard("")
            superseded: list[Chunk] = []
            if supersedes_document_id is not None:
                cached = self._data.get((tenant_ctx.tenant_id, collection_id))
                if cached is not None:
                    superseded = [
                        c for c in cached.chunks if c.document_id == supersedes_document_id
                    ]
                    cached.chunks = [
                        c for c in cached.chunks if c.document_id != supersedes_document_id
                    ]
                    cached.collection.document_count = len(
                        {c.document_id for c in cached.chunks}
                    )
            scope = replacement_id if duplicates_within_document else None
            if doc_hashes and self._document_id_by_hash_memory(
                next(iter(doc_hashes)), tenant_ctx.tenant_id, collection_id, scope
            ) is not None:
                if superseded:  # all or nothing: put the superseded copy back
                    cached = self._data[(tenant_ctx.tenant_id, collection_id)]
                    cached.chunks.extend(superseded)
                    cached.collection.document_count = len(
                        {c.document_id for c in cached.chunks}
                    )
                raise DuplicateContentError(
                    f"Content already indexed in collection {collection_id}"
                )
            if replacement_id is not None:
                self.delete_document(
                    replacement_id, collection_id=collection_id, tenant_ctx=tenant_ctx
                )
            for chunk in chunks:
                chunk.metadata = {**dict(chunk.metadata or {}), "document_id": chunk.document_id}
                self.ingest_chunk(chunk, collection_id=collection_id, tenant_ctx=tenant_ctx)
            if chunks:
                await self._notify_changed(tenant_ctx.tenant_id)
            return [chunk.chunk_id for chunk in chunks]

        records = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "content": chunk.content,
                "embedding": chunk.embedding,
                # Search results carry only the chunk's metadata, so the
                # document id rides along (RW-09: hits had document_id=null).
                "metadata": {**dict(chunk.metadata), "document_id": chunk.document_id},
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
            replacement_document_id=replacement_id,
            check_duplicates_on_replace=True,
            duplicates_within_document=duplicates_within_document,
            superseded_document_id=supersedes_document_id,
        )

        # In-memory mirror only when there is no database. With a DB the chunk
        # rows are the source of truth for both search (hybrid_search_db) and the
        # collection counters, and nothing reads this mirror back — while
        # extending it kept every chunk this process ever ingested on the heap
        # (an unbounded per-replica leak at corpus scale) and recomputed
        # document_count with a set comprehension over the whole list, making a
        # bulk load O(n^2) in Python on top of the O(n^2) it was doing in SQL.
        if self._db is None:
            cached = self._data.get((tenant_ctx.tenant_id, collection_id))
            if cached is not None:
                cached.chunks.extend(chunks)
                cached.collection.document_count = len(
                    {chunk.document_id for chunk in cached.chunks}
                )
        if chunks:
            await self._notify_changed(tenant_ctx.tenant_id)
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
                # Search results carry only the chunk's metadata, so the
                # document id rides along (RW-09: hits had document_id=null).
                "metadata": {**dict(chunk.metadata), "document_id": chunk.document_id},
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
        # In-memory mirror only when there is no database. With a DB the chunk
        # rows are the source of truth for both search (hybrid_search_db) and the
        # collection counters, and nothing reads this mirror back — while
        # extending it kept every chunk this process ever ingested on the heap
        # (an unbounded per-replica leak at corpus scale) and recomputed
        # document_count with a set comprehension over the whole list, making a
        # bulk load O(n^2) in Python on top of the O(n^2) it was doing in SQL.
        if self._db is None:
            cached = self._data.get((tenant_ctx.tenant_id, collection_id))
            if cached is not None:
                cached.chunks.extend(chunks)
                cached.collection.document_count = len(
                    {chunk.document_id for chunk in cached.chunks}
                )
        await self._notify_changed(tenant_ctx.tenant_id)
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

    @staticmethod
    async def _raise_if_doc_hash_indexed(
        session: Any,
        table: str,
        records: list[dict[str, Any]],
        *,
        collection_id: str,
        tenant_id: str,
        document_id: str | None = None,
    ) -> None:
        from sqlalchemy import text

        doc_hashes = {str(record["metadata"].get("doc_content_hash") or "") for record in records}
        doc_hashes.discard("")
        document_clause = "AND document_id = :document_id " if document_id is not None else ""
        for doc_hash in doc_hashes:
            params: dict[str, Any] = {
                "collection_id": collection_id,
                "tenant_id": tenant_id,
                "doc_hash": doc_hash,
            }
            if document_id is not None:
                params["document_id"] = document_id
            duplicate = (
                await session.execute(
                    text(
                        f"SELECT 1 FROM {table} "
                        "WHERE collection_id = :collection_id "
                        "AND tenant_id = :tenant_id "
                        "AND metadata->>'doc_content_hash' = :doc_hash "
                        f"{document_clause}"
                        "LIMIT 1"
                    ),
                    params,
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise DuplicateContentError(
                    f"Content already indexed in collection {collection_id} "
                    f"(doc_content_hash={doc_hash[:12]}...)"
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
        check_duplicates_on_replace: bool = False,
        duplicates_within_document: bool = False,
        superseded_document_id: str | None = None,
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
            vector_literal = "[" + ",".join(f"{value:.9g}" for value in record["embedding"]) + "]"
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
                raise EmbeddingDimensionError(
                    f"Collection {collection_id} uses {stored_dimension}-dimensional "
                    f"embeddings but the active embedder produces {dimension}-dimensional "
                    "vectors; re-embed the collection or use a new one"
                )
            # The first write fixes what the collection holds: its vector width
            # and (USR-3) the model those vectors came from — the label set at
            # create time may predate the embedder that actually wrote them.
            batch_model = _batch_embedding_model(records) or self._embedder_name
            if not chunk_count and batch_model:
                await session.execute(
                    text(
                        "UPDATE knowledge_collections SET embedding_dim = :dimension, "
                        "embedder = :embedder, updated_at = now() "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {
                        "dimension": dimension,
                        "embedder": batch_model,
                        "id": collection_id,
                        "tid": tenant_id,
                    },
                )
            elif not chunk_count and stored_dimension != dimension:
                await session.execute(
                    text(
                        "UPDATE knowledge_collections SET embedding_dim = :dimension, "
                        "updated_at = now() WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"dimension": dimension, "id": collection_id, "tid": tenant_id},
                )

            replaced_ids = [
                d for d in (replacement_document_id, superseded_document_id) if d is not None
            ]
            if replaced_ids:
                # P1d-5: a replacement deletes the previous version; a held
                # document (document, collection or tenant-wide hold) is never
                # replaced — checked here, in the replacing transaction, for every
                # path (URL re-ingest, re-upload, connector re-sync).
                held = (
                    await session.execute(
                        text(_REPLACED_HELD_SQL.format(table=table)),
                        {"ids": replaced_ids, "cid": collection_id, "tid": tenant_id},
                    )
                ).fetchone()
                if held is not None:
                    raise KnowledgeLegalHoldError(
                        f"document {held[0]} of collection {collection_id} is under legal "
                        "hold; it cannot be replaced"
                    )
            removed_chunks = 0
            removed_bytes = 0
            superseded_chunks = 0
            superseded_bytes = 0
            if superseded_document_id is not None:
                # D2: the document this one takes over goes first, inside this
                # transaction (and so before the duplicate guard below).
                gone = (
                    await session.execute(
                        text(f"""
                            DELETE FROM {table}
                            WHERE collection_id = :collection_id
                              AND tenant_id = :tenant_id
                              AND document_id = :document_id
                            RETURNING octet_length(content), chunk_index, id
                        """),
                        {
                            "collection_id": collection_id,
                            "tenant_id": tenant_id,
                            "document_id": superseded_document_id,
                        },
                    )
                ).fetchall()
                if gone:
                    from app.rag.retention import delete_document_graph

                    await delete_document_graph(
                        session, tenant_id, superseded_document_id, [tuple(r) for r in gone]
                    )
                superseded_chunks = len(gone)
                superseded_bytes = sum(int(r[0] or 0) for r in gone)
            if replacement_document_id is not None and check_duplicates_on_replace:
                # A replacement whose content is already indexed (this document
                # unchanged, or the same content under another id) is a duplicate,
                # exactly as on the plain insert path below.
                await self._raise_if_doc_hash_indexed(
                    session,
                    table,
                    records,
                    collection_id=collection_id,
                    tenant_id=tenant_id,
                    document_id=replacement_document_id if duplicates_within_document else None,
                )
            if replacement_document_id is not None:
                # RETURNING the byte sizes lets the counter update below be an
                # exact delta instead of a full-collection recompute.
                removed = (
                    await session.execute(
                        text(f"""
                            DELETE FROM {table}
                            WHERE collection_id = :collection_id
                              AND tenant_id = :tenant_id
                              AND document_id = :document_id
                            RETURNING octet_length(content)
                        """),
                        {
                            "collection_id": collection_id,
                            "tenant_id": tenant_id,
                            "document_id": replacement_document_id,
                        },
                    )
                ).fetchall()
                removed_chunks = len(removed)
                removed_bytes = sum(int(r[0] or 0) for r in removed)
            else:
                # TOCTOU guard: an earlier ``exists_by_hash`` dedup check (pipeline
                # Stage 3, RPA/OCR pre-checks) ran in its own, now-closed
                # transaction — a concurrent identical ingestion (a retry racing
                # the original attempt, or a re-sync overlapping a manual sync)
                # can pass that same check before either one commits. Re-check
                # ``doc_content_hash`` here, inside the transaction that holds
                # this collection's row lock, so the loser is caught atomically
                # instead of racing into a duplicate insert (or, when document_id
                # also matches, an ugly unique-constraint failure).
                await self._raise_if_doc_hash_indexed(
                    session, table, records, collection_id=collection_id, tenant_id=tenant_id
                )

            # How many of this batch's documents are *new* to the collection —
            # computed before the INSERT so the counter update below can be a
            # delta. One indexed probe per distinct document_id (the
            # uq_chunk_<dim> UNIQUE (collection_id, document_id, chunk_index)
            # index covers it); a batch is one document in every current caller.
            batch_document_ids = {str(record["document_id"]) for record in records}
            already_present = 0
            for document_id in batch_document_ids:
                seen = (
                    await session.execute(
                        text(
                            f"SELECT 1 FROM {table} WHERE collection_id = :cid "
                            "AND tenant_id = :tid AND document_id = :did LIMIT 1"
                        ),
                        {"cid": collection_id, "tid": tenant_id, "did": document_id},
                    )
                ).scalar_one_or_none()
                if seen is not None:
                    already_present += 1
            added_documents = len(batch_document_ids) - already_present
            added_chunks = len(parameters)
            added_bytes = sum(len(str(record["content"]).encode("utf-8")) for record in records)

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
            # Incremental, not recomputed. The previous version ran three
            # correlated aggregates over the collection's entire chunk table on
            # every single document — including sum(octet_length(content)), which
            # detoasts every chunk — making a bulk load O(N²). At a million
            # documents (~10M chunks) one further ingest read 30M rows. These
            # deltas are exact because the collection row is held under FOR
            # UPDATE for this whole transaction, so no concurrent ingest or
            # delete can interleave between the counts above and this update.
            await session.execute(
                text("""
                    UPDATE knowledge_collections
                    SET chunk_count = GREATEST(chunk_count + :d_chunks, 0),
                        document_count = GREATEST(document_count + :d_documents, 0),
                        total_size_bytes = GREATEST(
                            COALESCE(total_size_bytes, 0) + :d_bytes, 0
                        ),
                        last_indexed_at = now(),
                        updated_at = now()
                    WHERE id = :id AND tenant_id = :tid
                """),
                {
                    "id": collection_id,
                    "tid": tenant_id,
                    "d_chunks": added_chunks - removed_chunks - superseded_chunks,
                    # A replaced document was deleted just above, so the probe
                    # counted it as new again; it is the same document, so net 0.
                    # Only discount it when the delete actually removed rows —
                    # replacing a document the collection never had really is an
                    # addition. A superseded document (D2) is one document fewer.
                    "d_documents": added_documents
                    - (1 if removed_chunks else 0)
                    - (1 if superseded_chunks else 0),
                    "d_bytes": added_bytes - removed_bytes - superseded_bytes,
                },
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
