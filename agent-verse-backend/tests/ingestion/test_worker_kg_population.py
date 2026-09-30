"""KB-06: scheduled and DLQ-retried syncs populate the knowledge graph.

The Celery worker built its IngestionPipeline without a ``kg_hook``, so only
documents ingested through the API process ever produced graph entities.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import IngestionJob, RawDocument, SourceConfig, SourceFamily
from app.knowledge_graph.ingestion_hook import KGIngestionHook
from app.knowledge_graph.store import KnowledgeGraphStore
from app.providers.embedder_factory import EmbedderResolution

_TENANT = "kb06-tenant"
_BODY = (
    "Alice Johnson runs the ACME platform group in Berlin, and Bob Smith "
    "reviews every OpenAI integration the group ships each quarter."
)


class _MemoryKG(KnowledgeGraphStore):
    """Records the worker DB factory but stays in memory for the assertion."""

    last: _MemoryKG | None = None

    def set_db(self, db_factory: Any) -> None:
        self.bound_factory = db_factory
        _MemoryKG.last = self


class _NoRules:
    def __init__(self, _factory: Any) -> None: ...

    async def load(self, _tenant: str) -> list[Any]:
        return []

    async def upsert(self, _rule: Any) -> None: ...

    async def insert_if_absent(self, _rule: Any) -> None: ...


class _Store:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]

    def add_change_listener(self, _listener: Any) -> None: ...


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="fake")


class _Connector:
    async def get_delta(self, config: Any, cursor: Any) -> Any:
        yield (
            RawDocument(
                doc_id="doc-kb06",
                source_id="src-kb06",
                tenant_id=_TENANT,
                content=_BODY.encode(),
                content_type="text/plain",
            ),
            "c1",
        )


def _build(fake_factory: Any) -> tuple[Any, Any, Any]:
    from app.ingestion.scheduler import _build_worker_ingestion

    with (
        patch("app.db.session.get_session_factory", return_value=fake_factory),
        patch("app.db.session.get_system_session_factory", return_value=MagicMock()),
        patch(
            "app.providers.embedder_factory.resolve_embedder",
            return_value=EmbedderResolution(embedder=_Embedder(), dimension=8),
        ),
        patch("app.rag.store.KnowledgeStore", return_value=_Store()),
        patch("app.guardrails_v2.repository.PostgresGuardrailRuleRepository", _NoRules),
        patch("app.knowledge_graph.store.KnowledgeGraphStore", _MemoryKG),
    ):
        return _build_worker_ingestion()


def test_worker_pipeline_has_a_db_backed_kg_hook() -> None:
    factory = MagicMock(name="worker-factory")
    _tracker, pipeline, _src = _build(factory)
    assert isinstance(pipeline, IngestionPipeline)
    assert isinstance(pipeline._kg_hook, KGIngestionHook)
    assert _MemoryKG.last is not None
    assert _MemoryKG.last.bound_factory is factory
    assert pipeline._kg_hook._store is _MemoryKG.last


async def test_scheduled_sync_creates_kg_nodes_for_the_document() -> None:
    from app.ingestion.scheduler import _sync_source_async

    _tracker, pipeline, _src = _build(MagicMock())
    pipeline._quota = None  # no DB here; quota is covered elsewhere
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(
        return_value=IngestionJob(
            job_id="j",
            source_id="src-kb06",
            tenant_id=_TENANT,
            status="running",
            sync_mode="incremental",
        )
    )
    source_store = AsyncMock()
    source_store.get = AsyncMock(
        return_value=SourceConfig(
            source_id="src-kb06",
            tenant_id=_TENANT,
            name="n",
            family=SourceFamily.WEB,
            source_type="http",
            collection_id="col",
            min_quality_score=0.0,
        )
    )
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, source_store),
        ),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-kb06", tenant_id=_TENANT, triggered_by="scheduler"
        )

    assert result["docs_indexed"] == 1, result
    kg = pipeline._kg_hook._store
    labels = {n.label for n in kg.query_nodes(tenant_id=_TENANT)}
    assert {"Alice Johnson", "Bob Smith"} <= labels
