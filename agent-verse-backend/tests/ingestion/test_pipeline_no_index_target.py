"""A document with nowhere to be indexed must not be reported ``indexed``.

Regression: ``IngestionPipeline._index`` returned ``[]`` when no
``collection_id`` (or no knowledge store) was configured, and the pipeline then
set ``status="indexed"`` with 0 chunks — a sync reported success having written
nothing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily


def _raw() -> RawDocument:
    return RawDocument(
        doc_id="d1",
        source_id="s1",
        tenant_id="t1",
        content=b"A reasonably long paragraph of text about ingestion. " * 20,
        content_type="text/plain",
    )


def _config(collection_id: str | None) -> SourceConfig:
    return SourceConfig(  # type: ignore[call-arg]
        source_id="s1",
        tenant_id="t1",
        name="T",
        family=SourceFamily.WEB,
        source_type="test",
        collection_id=collection_id,
    )


def _pipeline(kb: Any) -> IngestionPipeline:
    pipeline = IngestionPipeline(knowledge_store=kb, embedder=MagicMock())

    async def _embed(chunks: list[dict], config: SourceConfig) -> list[dict]:
        return [{**c, "embedding": [0.1] * 8} for c in chunks]

    pipeline._embed = _embed  # type: ignore[method-assign]
    pipeline._check_existing_hash = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return pipeline


@pytest.mark.asyncio
async def test_no_collection_is_failed_not_indexed() -> None:
    kb = MagicMock()
    result = await _pipeline(kb).ingest(_raw(), _config(None))
    assert result.status == "failed", (result.status, result.skip_reason)
    assert "collection_id" in (result.error or "")
    assert result.chunks_created == 0


@pytest.mark.asyncio
async def test_no_knowledge_store_is_never_indexed() -> None:
    result = await IngestionPipeline().ingest(_raw(), _config("col-1"))
    assert result.status != "indexed"
