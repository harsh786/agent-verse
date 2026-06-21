"""In-memory knowledge store with hybrid search (cosine 70% + trigram 30%).

In production this is backed by PostgreSQL + pgvector (HNSW index) and pg_trgm.
This pure-Python implementation is used in tests and as a fallback.

Hybrid search formula:
  score = 0.7 * cosine_similarity(query_vec, chunk_vec)
        + 0.3 * trigram_overlap(query_text, chunk_text)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.rag.models import Chunk, KnowledgeCollection
from app.tenancy.context import TenantContext

_VECTOR_WEIGHT = 0.7
_TRIGRAM_WEIGHT = 0.3


@dataclass
class HybridSearchResult:
    chunk_id: str
    content: str
    score: float
    vector_score: float
    trigram_score: float


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
    """

    def __init__(self) -> None:
        # Key: (tenant_id, collection_id) → _CollectionStore
        self._data: dict[tuple[str, str], _CollectionStore] = {}

    def create_collection(
        self, collection: KnowledgeCollection, *, tenant_ctx: TenantContext
    ) -> str:
        key = (tenant_ctx.tenant_id, collection.collection_id)
        self._data[key] = _CollectionStore(collection=collection)
        return collection.collection_id

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

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[HybridSearchResult]:
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
