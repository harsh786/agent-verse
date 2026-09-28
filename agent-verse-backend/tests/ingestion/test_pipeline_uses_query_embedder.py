"""The ingestion pipeline must embed documents with the query embedder.

Retrieval embeds queries with ``app.state.embedder``; the pipeline was built with
the chat LLM provider (``_app_provider`` in ``create_app``, ``resolve_provider()``
in the Celery worker). Different model, different vector space: every similarity
score between a query and an ingested document was meaningless, and nothing
failed. Each chunk also records the ``embedding_model`` that produced it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.embedder_factory import build_query_embedder, embedder_model_name


def test_create_app_pipeline_embedder_is_the_query_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding-8b")
    from app.main import create_app

    app = create_app()
    pipeline = app.state.ingestion_pipeline
    assert pipeline is not None
    assert app.state.embedder is not None
    assert pipeline._embedder is app.state.embedder
    assert embedder_model_name(pipeline._embedder) == "qwen3-embedding-8b"


def test_worker_builds_the_same_embedder_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding-8b")
    from app.ingestion.scheduler import _build_worker_ingestion

    with patch("app.db.session.get_session_factory"), patch(
        "app.db.session.get_system_session_factory"
    ):
        _tracker, pipeline, _store = _build_worker_ingestion()
    assert embedder_model_name(pipeline._embedder) == "qwen3-embedding-8b"  # type: ignore[attr-defined]
    assert embedder_model_name(build_query_embedder()) == "qwen3-embedding-8b"


@pytest.mark.asyncio
async def test_chunks_record_embedding_model() -> None:
    kb = MagicMock()
    kb.exists_by_hash = AsyncMock(return_value=False)
    kb.ingest_chunks_async = AsyncMock(return_value=["c1"])
    embedder = MagicMock()
    embedder._embed_model_name = "voyage-3-large"
    pipeline = IngestionPipeline(knowledge_store=kb, embedder=embedder)
    pipeline._parser_registry.parse_bytes_async = AsyncMock(  # type: ignore[method-assign]
        return_value=("A reasonably long document body for the embedding test. " * 5, {})
    )
    raw = RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1", content=b"x", content_type="text/plain"
    )
    cfg = SourceConfig(
        source_id="s1", tenant_id="t1", name="n", family=SourceFamily.WEB,
        source_type="http", collection_id="col",
    )
    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 4] * 8)):
        result = await pipeline.ingest(raw, cfg)
    assert result.status == "indexed", result.error
    chunks = kb.ingest_chunks_async.call_args[0][0]
    assert chunks
    assert all(c.metadata["embedding_model"] == "voyage-3-large" for c in chunks)
