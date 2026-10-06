"""S3 document ids are scoped to the Source (and bucket), with a legacy-id takeover.

The id used to be the bare ``s3://bucket/key``: two S3 Sources reading the same
object into ONE collection (or two endpoints with the same bucket name) shared
one document — the second was dedup-skipped / replaced the first, and it stayed
attributed to whichever Source wrote it. Now each Source gets its own
document. Compat: a document indexed under the legacy id is taken over (deleted
in the same transaction) when ITS Source re-fetches the object; another
Source's legacy copy is never touched.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

from app.ingestion.connectors.s3_connector import (
    S3Connector,
    s3_document_id,
    s3_legacy_document_id,
)
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import (
    CONNECTOR_LEGACY_DOC_ID_KEY,
    RawDocument,
    SourceConfig,
    SourceFamily,
)
from app.providers.fake import FakeProvider
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.ingestion.test_sdk_calls_off_loop import _fake_boto3

_TENANT = "t-s3ns"
_CTX = TenantContext(tenant_id=_TENANT, api_key_id="test", plan=PlanTier.FREE)


def _cfg(source_id: str, bucket: str = "b") -> SourceConfig:
    return SourceConfig(
        source_id=source_id, tenant_id=_TENANT, name=source_id,
        family=SourceFamily.OBJECT_STORAGE, source_type="s3", collection_id="c1",
        connection_config={"bucket": bucket},
    )


def _store() -> KnowledgeStore:
    store = KnowledgeStore()
    store.create_collection(KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_CTX)
    return store


async def _sync(cfg: SourceConfig, pipeline: IngestionPipeline) -> list[Any]:
    results = []
    with patch.dict(sys.modules, _fake_boto3()):
        async for doc, _cursor in S3Connector().get_delta(cfg, None):
            results.append(await pipeline.ingest(doc, cfg))
    return results


def _documents(store: KnowledgeStore) -> dict[str, set[str]]:
    """document_id -> the source ids its chunks are attributed to."""
    out: dict[str, set[str]] = {}
    for chunk in store._data[(_TENANT, "c1")].chunks:
        out.setdefault(chunk.document_id, set()).add(str(chunk.metadata.get("source_id")))
    return out


def test_ids_differ_per_source_and_bucket_and_are_stable() -> None:
    a, b = _cfg("src-a"), _cfg("src-b")
    assert s3_document_id(a, "b", "k.txt") == s3_document_id(_cfg("src-a"), "b", "k.txt")
    assert s3_document_id(a, "b", "k.txt") != s3_document_id(b, "b", "k.txt")
    assert s3_document_id(a, "b", "k.txt") != s3_document_id(a, "other", "k.txt")
    assert s3_legacy_document_id("b", "k.txt") == "s3://b/k.txt"


async def test_two_sources_over_the_same_object_get_their_own_documents() -> None:
    store = _store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    first = await _sync(_cfg("src-a"), pipeline)
    second = await _sync(_cfg("src-b"), pipeline)
    assert [r.status for r in first] == ["indexed"]
    assert [r.status for r in second] == ["indexed"]  # no longer dedup-skipped
    assert _documents(store) == {
        s3_document_id(_cfg("src-a"), "b", "a.txt"): {"src-a"},
        s3_document_id(_cfg("src-b"), "b", "a.txt"): {"src-b"},
    }
    # Re-syncing an unchanged object is still a no-op for each Source.
    again = await _sync(_cfg("src-a"), pipeline)
    assert [(r.status, r.skip_reason) for r in again] == [("skipped", "dedup")]


async def _seed_legacy(store: KnowledgeStore, source_id: str, content: str) -> str:
    legacy = s3_legacy_document_id("b", "a.txt")
    await store.ingest_chunks_async(
        [
            Chunk(
                document_id=legacy, content=content, embedding=[0.1] * 768, chunk_index=0,
                metadata={"source_id": source_id, "doc_content_hash": "legacy-hash"},
            )
        ],
        collection_id="c1", tenant_ctx=_CTX,
    )
    return legacy


async def test_own_legacy_document_is_taken_over_on_refetch() -> None:
    store = _store()
    legacy = await _seed_legacy(store, "src-a", "old version of a.txt")
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    (result,) = await _sync(_cfg("src-a"), pipeline)
    assert result.status == "indexed"
    assert result.metadata["superseded_legacy_id"] == legacy  # type: ignore[attr-defined]
    assert _documents(store) == {s3_document_id(_cfg("src-a"), "b", "a.txt"): {"src-a"}}


async def test_another_sources_legacy_document_is_left_alone() -> None:
    store = _store()
    legacy = await _seed_legacy(store, "src-a", "src-a's copy")
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    (result,) = await _sync(_cfg("src-b"), pipeline)
    assert result.status == "indexed"
    assert "superseded_legacy_id" not in result.metadata  # type: ignore[attr-defined]
    assert _documents(store) == {
        legacy: {"src-a"},
        s3_document_id(_cfg("src-b"), "b", "a.txt"): {"src-b"},
    }


async def test_legacy_key_is_ignored_when_it_names_the_document_itself() -> None:
    store = _store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    doc = RawDocument(
        doc_id="x", source_id="src-a", tenant_id=_TENANT, content=b"Some content here",
        content_type="text/plain", metadata={CONNECTOR_LEGACY_DOC_ID_KEY: "x"},
    )
    result = await pipeline.ingest(doc, _cfg("src-a"))
    assert result.status == "indexed"
    assert "superseded_legacy_id" not in result.metadata  # type: ignore[attr-defined]
