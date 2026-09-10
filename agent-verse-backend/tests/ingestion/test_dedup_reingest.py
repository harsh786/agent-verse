"""WS-12 [TOP FIX] — end-to-end document dedup via KnowledgeStore.exists_by_hash.

Drives the real ``IngestionPipeline`` against a real in-memory ``KnowledgeStore``
to prove that:
  * a document is indexed with the document-level hash on every chunk
    (``doc_content_hash`` metadata), and
  * re-ingesting the SAME document is skipped as a dedup (the check that was
    dead before ``exists_by_hash`` existed).
Also pins parse/OCR provenance persistence (item 4) and the no-zero-vector
embedder-failure path (item 5).
"""

from __future__ import annotations

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, api_key_id="test", plan=PlanTier.FREE)


def _store() -> KnowledgeStore:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_ctx("t1")
    )
    return store


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="s1",
        tenant_id="t1",
        name="T",
        family=SourceFamily.WEB,
        source_type="test",
        collection_id="c1",
    )


def _doc(content: bytes) -> RawDocument:
    return RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1", content=content,
        content_type="text/plain",
    )


@pytest.mark.asyncio
async def test_reingest_same_document_is_deduped() -> None:
    store = _store()
    pipeline = IngestionPipeline(
        knowledge_store=store, embedder=FakeProvider(embed_dim=768)
    )
    content = ("World-class dedup content. " * 40).encode()

    first = await pipeline.ingest(_doc(content), _config())
    assert first.status == "indexed"
    assert first.chunks_created > 0

    # doc_content_hash is stamped on every stored chunk.
    stored = store._data[("t1", "c1")].chunks
    assert stored
    assert all(c.metadata.get("doc_content_hash") for c in stored)

    # Re-ingesting the identical document must dedup (skip), not re-index.
    second = await pipeline.ingest(_doc(content), _config())
    assert second.status == "skipped"
    assert second.skip_reason == "dedup"
    # No new chunks written on the deduped re-ingest.
    assert len(store._data[("t1", "c1")].chunks) == len(stored)


@pytest.mark.asyncio
async def test_different_document_is_not_deduped() -> None:
    store = _store()
    pipeline = IngestionPipeline(
        knowledge_store=store, embedder=FakeProvider(embed_dim=768)
    )
    r1 = await pipeline.ingest(
        _doc(("Alpha content alpha. " * 40).encode()), _config()
    )
    r2 = await pipeline.ingest(
        _doc(("Beta content beta different. " * 40).encode()), _config()
    )
    assert r1.status == "indexed"
    assert r2.status == "indexed"


@pytest.mark.asyncio
async def test_provenance_persisted_on_chunks() -> None:
    """OCR/degradation provenance recorded at parse survives onto indexed chunks."""
    store = _store()
    pipeline = IngestionPipeline(
        knowledge_store=store, embedder=FakeProvider(embed_dim=768)
    )

    async def _fake_parse(content, content_type, **kwargs):  # type: ignore[no-untyped-def]
        return (
            "Recovered OCR text " * 30,
            {"ocr_used": True, "ocr_engine": "tesseract", "pdf_degraded": "no fitz"},
        )

    pipeline._parser_registry.parse_bytes_async = _fake_parse  # type: ignore[assignment]
    result = await pipeline.ingest(_doc(b"%PDF-1.4 scanned bytes"), _config())
    assert result.status == "indexed"

    chunks = store._data[("t1", "c1")].chunks
    assert chunks
    prov = chunks[0].metadata.get("ingestion_provenance")
    assert isinstance(prov, dict)
    assert prov.get("ocr_used") is True
    assert prov.get("ocr_engine") == "tesseract"
    assert chunks[0].metadata.get("ocr_used") is True


@pytest.mark.asyncio
async def test_no_zero_vector_chunks_written_when_embedder_fails() -> None:
    """An embedder that yields no vectors must never persist silent zero/empty vectors."""
    store = _store()
    pipeline = IngestionPipeline(
        knowledge_store=store, embedder=FakeProvider(embed_dim=768)
    )

    async def _empty_embed(chunks, config):  # type: ignore[no-untyped-def]
        for c in chunks:
            c["embedding"] = []
        return chunks

    pipeline._embed = _empty_embed  # type: ignore[assignment]
    result = await pipeline.ingest(
        _doc(("Important content. " * 40).encode()), _config()
    )

    # Never "indexed" with empty/zero vectors — either skipped or failed, honestly.
    assert result.status in ("skipped", "failed")
    # And nothing was written to the store.
    assert store._data[("t1", "c1")].chunks == []
