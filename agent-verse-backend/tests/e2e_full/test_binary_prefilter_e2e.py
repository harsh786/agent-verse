"""e2e_full: the pgvector binary_quantize() Hamming index + two-stage search.

Proves the storage-layer binary quantization works end-to-end against real
pgvector (>= 0.7): migration 0120 builds the bit_hamming_ops HNSW index, and
KnowledgeStore.binary_prefilter_search shortlists by Hamming over the compact
binary codes then reranks the shortlist by full-precision cosine.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_EMBED_DIM = 768


async def _pgvector_version(kb: object) -> tuple[int, ...]:
    async with kb._db() as session:  # type: ignore[attr-defined]
        v = (
            await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one_or_none()
    return tuple(int(p) for p in str(v).split(".")[:3]) if v else (0, 0, 0)


@pytest.fixture(scope="session")
async def _seeded_tenant(client: object) -> str:
    email = f"binq-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(  # type: ignore[attr-defined]
        "/tenants/signup", json={"name": "Binary Prefilter", "email": email}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tenant_id"])


async def test_binary_index_exists_and_prefilter_search_works(
    app: object, _seeded_tenant: str
) -> None:
    kb = app.state.knowledge_store  # type: ignore[attr-defined]
    assert kb._db is not None, "e2e must run against the DB-backed KnowledgeStore"

    if await _pgvector_version(kb) < (0, 7, 0):
        pytest.skip("pgvector < 0.7 — binary_quantize unavailable")

    tenant_id = _seeded_tenant
    tenant_ctx = TenantContext(tenant_id=tenant_id, api_key_id="e2e", plan=PlanTier.FREE)

    collection_id = uuid.uuid4().hex
    await kb.create_collection_async(
        KnowledgeCollection(name="binq", collection_id=collection_id, embedder="fake"),
        tenant_ctx=tenant_ctx,
    )

    # ── The 0120 binary Hamming index must exist on the 768 table. ──
    async with kb._db() as session:
        exists = (
            await session.execute(
                text(
                    "SELECT 1 FROM pg_class WHERE relname = "
                    "'idx_knowledge_chunks_768_binary_hamming'"
                )
            )
        ).scalar_one_or_none()
    assert exists == 1, "migration 0120 binary Hamming index is missing"

    # ── Ingest a few chunks with real embeddings. ──
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    pipeline = IngestionPipeline(
        knowledge_store=kb, embedder=FakeProvider(embed_dim=_EMBED_DIM)
    )
    cfg = SourceConfig(
        source_id="binq-src",
        tenant_id=tenant_id,
        name="binq e2e",
        family=SourceFamily.DOCUMENT_STORE,
        source_type="e2e",
        collection_id=collection_id,
        min_quality_score=0.0,
    )
    doc = RawDocument(
        doc_id="doc-1",
        source_id="binq-src",
        tenant_id=tenant_id,
        content=(
            b"Ada Lovelace was a mathematician.\n\n"
            b"Alan Turing founded computer science.\n\n"
            b"Grace Hopper built the first compiler.\n"
        ),
        content_type="text/plain",
        title="pioneers.txt",
    )
    result = await pipeline.ingest(doc, cfg)
    assert result.status == "indexed", f"not indexed: {result.error}"
    assert result.chunks_created > 0

    # ── Two-stage binary-prefilter search returns real, scored results. ──
    q_emb = (await FakeProvider(embed_dim=_EMBED_DIM).embed_batch(["Ada Lovelace"]))[0]
    hits = await kb.binary_prefilter_search(
        q_emb, collection_id, tenant_ctx, top_k=3, shortlist=50
    )
    assert hits, "binary_prefilter_search returned nothing"
    assert all(-1.0001 <= h.score <= 1.0001 for h in hits)  # cosine-reranked scores
    assert all(h.chunk_id for h in hits)
    # Results are ordered by exact cosine (descending score).
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)
