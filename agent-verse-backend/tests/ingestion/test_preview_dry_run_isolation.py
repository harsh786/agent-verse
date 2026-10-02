"""PREVIEW-RACE: a Source preview must not switch the shared pipeline into dry-run.

``POST /sources/{id}/preview`` set ``pipeline._dry_run = True`` on the app-wide
``IngestionPipeline`` for the whole preview, so any other request ingesting on
the same replica meanwhile got ``dry_run`` back (nothing indexed), and a
concurrent preview finishing first switched another preview back to indexing.
Dry-run is now a per-call argument.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import app.api.ingestion as ingestion_mod
from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from tests.api.test_ingestion_api import _CTX, _auth, _make_app


def _one_vector_per_text(dim: int):  # type: ignore[no-untyped-def]
    """embed_texts stand-in that returns exactly one vector per input text.

    KB-40's metered embedding refuses a batch whose result count differs from
    its input count, so a fixed-size canned list fails real ingestion.
    """

    async def _embed(texts, **_kwargs):  # type: ignore[no-untyped-def]
        return [[0.1] * dim for _ in texts]

    return _embed

_TEXT = b"A paragraph long enough to be chunked and indexed by the pipeline. " * 8


def _doc(doc_id: str, source_id: str) -> RawDocument:
    return RawDocument(
        doc_id=doc_id,
        source_id=source_id,
        tenant_id=_CTX.tenant_id,
        content=_TEXT + doc_id.encode(),
        content_type="text/plain",
    )


def _pipeline() -> tuple[IngestionPipeline, MagicMock]:
    kb = MagicMock()
    kb.exists_by_hash = AsyncMock(return_value=False)
    kb.ingest_chunks_async = AsyncMock(return_value=["c1"])
    return IngestionPipeline(knowledge_store=kb, embedder=MagicMock()), kb


async def test_dry_run_is_per_call_not_pipeline_state() -> None:
    pipeline, kb = _pipeline()
    config = SourceConfig(
        source_id="s", tenant_id=_CTX.tenant_id, name="s", family=SourceFamily.WEB,
        source_type="http", collection_id="col",
    )
    with patch("app.providers.base.embed_texts", AsyncMock(side_effect=_one_vector_per_text(8))):
        dry = await pipeline.ingest(_doc("d1", "s"), config, dry_run=True)
        real = await pipeline.ingest(_doc("d2", "s"), config)
    assert dry.status == "dry_run"
    assert real.status == "indexed", real.error
    assert kb.ingest_chunks_async.await_count == 1


async def test_ingest_during_a_preview_still_indexes() -> None:
    """Two concurrent requests on one replica: a preview parked inside its
    connector, and another request ingesting through the same pipeline."""
    pipeline, kb = _pipeline()
    gate = asyncio.Event()
    preview_started = asyncio.Event()

    class _SlowConnector(BaseConnector):
        source_type = "slow-preview"

        async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
            return ConnectionHealth(ok=True)

        async def get_delta(
            self, config: SourceConfig, cursor: str | None
        ) -> AsyncIterator[tuple[RawDocument, str]]:
            preview_started.set()
            await gate.wait()
            yield _doc("preview-doc", config.source_id), "c1"

    source = SourceConfig(
        source_id="src-preview", tenant_id=_CTX.tenant_id, name="p",
        family=SourceFamily.WEB, source_type="slow-preview", collection_id="col",
    )
    ingestion_mod._SOURCES[source.source_id] = source
    app = _make_app(ingestion_pipeline=pipeline)
    try:
        with (
            patch("app.ingestion.connector_registry.get_connector", return_value=_SlowConnector),
            patch("app.providers.base.embed_texts", AsyncMock(side_effect=_one_vector_per_text(8))),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:

                async def _preview() -> Any:
                    return await client.post(
                        f"/sources/{source.source_id}/preview", headers=_auth()
                    )

                async def _concurrent_ingest() -> Any:
                    await preview_started.wait()
                    try:
                        return await pipeline.ingest(_doc("live-doc", source.source_id), source)
                    finally:
                        gate.set()

                preview_resp, live = await asyncio.gather(_preview(), _concurrent_ingest())
    finally:
        ingestion_mod._SOURCES.clear()

    assert live.status == "indexed", (live.status, live.error)
    assert preview_resp.status_code == 200, preview_resp.text
    body = preview_resp.json()
    assert body["docs_previewed"] == 1, body
    assert body["sample"][0]["status"] == "dry_run"
    # Only the live document reached the knowledge store.
    assert kb.ingest_chunks_async.await_count == 1
    assert not getattr(pipeline, "_dry_run", False)
