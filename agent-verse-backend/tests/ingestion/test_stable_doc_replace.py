"""STABLE-DOC-IDS: a re-synced document with the same id replaces its old version.

Connectors now derive document ids from the Source and the item's native
identity, so an edited upstream item arrives with the id it was indexed under.
The pipeline must then replace the old chunks (not add a second copy beside
them, and not hit the (collection, document, chunk_index) unique key), while an
unchanged item is still skipped by the content-hash dedup.
"""

from __future__ import annotations

import uuid

import pytest

from app.ingestion.base_connector import row_identity, stable_doc_id
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t1", api_key_id="test", plan=PlanTier.FREE)


def _config(source_id: str = "s1") -> SourceConfig:
    return SourceConfig(
        source_id=source_id, tenant_id="t1", name="T", family=SourceFamily.WEB,
        source_type="test", collection_id="c1",
    )


def _doc(doc_id: str, text: str) -> RawDocument:
    return RawDocument(
        doc_id=doc_id, source_id="s1", tenant_id="t1", content=(text * 40).encode(),
        content_type="text/plain",
    )


def test_stable_doc_id_is_deterministic_and_scoped_to_the_source() -> None:
    a = stable_doc_id(_config("s1"), "PROJ-1")
    assert a == stable_doc_id(_config("s1"), "PROJ-1")
    assert a != stable_doc_id(_config("s1"), "PROJ-2")
    assert a != stable_doc_id(_config("s2"), "PROJ-1")
    assert stable_doc_id(_config(), "a", "b") != stable_doc_id(_config(), "a\x1fb", "")
    uuid.UUID(a)


def test_row_identity_prefers_a_key_column_then_falls_back_to_a_row_hash() -> None:
    assert row_identity({"id": 7, "v": 1}) == "id=7"
    assert row_identity({"order_no": 9, "id": 7}, "order_no") == "order_no=9"
    keyless = row_identity({"a": 1, "b": "x"})
    assert keyless.startswith("row-sha256=")
    assert keyless == row_identity({"b": "x", "a": 1})
    assert keyless != row_identity({"a": 2, "b": "x"})


@pytest.mark.asyncio
async def test_edited_document_replaces_its_previous_version() -> None:
    store = KnowledgeStore()
    store.create_collection(KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_CTX)
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    doc_id = stable_doc_id(_config(), "PROJ-1")

    first = await pipeline.ingest(_doc(doc_id, "Original issue description. "), _config())
    assert first.status == "indexed"
    edited = await pipeline.ingest(_doc(doc_id, "Edited issue description! "), _config())
    assert edited.status == "indexed", edited.error

    chunks = [c for c in store._data[("t1", "c1")].chunks if c.document_id == doc_id]
    assert chunks
    assert all("Edited" in c.content for c in chunks), "the stale version is still indexed"
    assert store._data[("t1", "c1")].collection.document_count == 1

    # Unchanged on the next sync: still a dedup skip, nothing rewritten.
    again = await pipeline.ingest(_doc(doc_id, "Edited issue description! "), _config())
    assert again.status == "skipped" and again.skip_reason == "dedup"


@pytest.mark.asyncio
async def test_other_documents_are_untouched_by_a_replacement() -> None:
    store = KnowledgeStore()
    store.create_collection(KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_CTX)
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    a, b = stable_doc_id(_config(), "A"), stable_doc_id(_config(), "B")
    await pipeline.ingest(_doc(a, "Document A body. "), _config())
    await pipeline.ingest(_doc(b, "Document B body. "), _config())
    await pipeline.ingest(_doc(a, "Document A edited. "), _config())

    by_doc: dict[str, set[str]] = {}
    for c in store._data[("t1", "c1")].chunks:
        by_doc.setdefault(c.document_id, set()).add(c.content[:16])
    assert set(by_doc) == {a, b}
    assert all(s.startswith("Document B") for s in by_doc[b])
    assert all(s.startswith("Document A edit") for s in by_doc[a])
