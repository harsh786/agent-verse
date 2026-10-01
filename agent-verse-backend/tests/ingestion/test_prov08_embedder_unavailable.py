"""PROV-08: an embedder without embed support fails ingestion with a clear reason.

``embed_texts`` turned an unsupported embedder into ``[]`` vectors and the
pipeline swallowed embed errors, so documents were "skipped" (or chunks stored
vectorless) and silently fell back to lexical search.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.base import EmbedderUnavailableError, embed_texts

_BODY = (
    "A perfectly ordinary paragraph about quarterly planning that is long enough "
    "to pass the quality gate and be chunked, embedded and indexed by the pipeline."
)


class _NoEmbed:
    async def embed(self, request: Any) -> Any:
        raise NotImplementedError("this provider has no embeddings endpoint")


class _Store:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]


async def test_embed_texts_raises_for_an_unsupported_embedder() -> None:
    with pytest.raises(EmbedderUnavailableError, match="embeddings"):
        await embed_texts(["x"], provider=_NoEmbed())  # type: ignore[arg-type]


async def test_embed_texts_raises_without_an_embedder() -> None:
    with pytest.raises(EmbedderUnavailableError):
        await embed_texts(["x"], provider=None)


async def test_ingestion_fails_with_the_embedder_reason_and_stores_nothing() -> None:
    store = _Store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_NoEmbed())
    doc = RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1", content=_BODY.encode(),
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="s1", tenant_id="t1", name="src", family=SourceFamily.WEB,
        source_type="test", collection_id="c1", min_quality_score=0.0,
    )
    result = await pipeline.ingest(doc, config)
    assert result.status == "failed"
    assert result.skip_reason == "embedding_unavailable"
    assert "no embeddings endpoint" in result.error
    assert store.chunks == []
