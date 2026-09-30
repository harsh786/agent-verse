"""KB-01: worker-ingested documents must be screened against the tenant's
persisted RAG_INGEST guardrail rules.

The FastAPI lifespan binds ``guardrails_engine`` to a
``PostgresGuardrailRuleRepository``; the Celery worker never did, so
``ensure_tenant_loaded`` was a no-op there and scheduled / DLQ-retried connector
syncs were screened only against the baseline defaults — a tenant's own GDPR /
PCI block rules never applied to worker-ingested documents.

Pins:
* ``_build_worker_ingestion`` binds the engine to a repository built on the
  worker's (RLS) session factory;
* a tenant rule held only in that repository blocks a document synced through
  ``_sync_source_async``;
* a rules load failure fails the document closed (DLQ), never indexes it;
* in production, screening with no repository bound refuses to run.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.guardrails_v2.engine import guardrails_engine
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule
from app.ingestion.pipeline import IngestionPipeline, IngestionScreeningUnavailableError
from app.ingestion.source_config import IngestionJob, RawDocument, SourceConfig, SourceFamily
from app.providers.embedder_factory import EmbedderResolution

_TENANT = "kb01-worker-tenant"
_SECRET_WORD = "projectnightingale"
_BODY = (
    "Internal planning memo for the next quarter. The codename "
    f"{_SECRET_WORD} must never leave the building and is covered by policy."
)


def _tenant_rule() -> GuardrailRule:
    return GuardrailRule(
        rule_id=f"custom:{_TENANT}:codename",
        tenant_id=_TENANT,
        name="block codename in RAG ingest",
        rule_type="keyword_block",
        layers=[GuardrailLayer.RAG_INGEST],
        action=GuardrailAction.BLOCK,
        config={"keywords": [_SECRET_WORD]},
    )


class _FakeRepo:
    """Stand-in for PostgresGuardrailRuleRepository (tenant rules in 'the DB')."""

    instances: list[_FakeRepo] = []

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory
        self.rows = [_tenant_rule()]
        self.fail_load = False
        _FakeRepo.instances.append(self)

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        if self.fail_load:
            raise ConnectionError("database unavailable")
        return [r for r in self.rows if r.tenant_id == tenant_id]

    async def upsert(self, rule: GuardrailRule) -> None:
        return None

    async def insert_if_absent(self, rule: GuardrailRule) -> None:
        return None


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
                doc_id="memo-1",
                source_id="src-kb01",
                tenant_id=_TENANT,
                content=_BODY.encode(),
                content_type="text/plain",
            ),
            "c1",
        )


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="src-kb01",
        tenant_id=_TENANT,
        name="memos",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id="col-1",
        min_quality_score=0.0,
        enabled=True,
    )


@pytest.fixture(autouse=True)
def _isolate_engine() -> Iterator[None]:
    """The engine is a process-global singleton: restore it after each test."""
    saved_repo, saved_auto = guardrails_engine._repo, guardrails_engine._auto_persist
    saved_rules = {k: list(v) for k, v in guardrails_engine._rules.items()}
    _FakeRepo.instances.clear()
    yield
    guardrails_engine.bind_repository(saved_repo, auto_persist=saved_auto)
    guardrails_engine._rules = saved_rules
    guardrails_engine._unsaved_seeds = []
    guardrails_engine._unsaved = []


def _build_patches(store: _Store, fake_factory: Any) -> list[Any]:
    return [
        patch("app.db.session.get_session_factory", return_value=fake_factory),
        patch("app.db.session.get_system_session_factory", return_value=MagicMock()),
        patch(
            "app.providers.embedder_factory.resolve_embedder",
            return_value=EmbedderResolution(embedder=_Embedder(), dimension=8),
        ),
        patch("app.rag.store.KnowledgeStore", return_value=store),
        patch("app.guardrails_v2.repository.PostgresGuardrailRuleRepository", _FakeRepo),
    ]


def test_worker_build_binds_the_guardrail_repository_to_the_worker_factory() -> None:
    from app.ingestion.scheduler import _build_worker_ingestion

    guardrails_engine.bind_repository(None)
    fake_factory = MagicMock(name="worker-session-factory")
    patches = _build_patches(_Store(), fake_factory)
    for p in patches:
        p.start()
    try:
        _build_worker_ingestion()
    finally:
        for p in reversed(patches):
            p.stop()
    assert guardrails_engine.has_repository
    assert len(_FakeRepo.instances) == 1
    assert _FakeRepo.instances[0].session_factory is fake_factory
    assert guardrails_engine._repo is _FakeRepo.instances[0]


async def _run_sync(store: _Store, *, fail_load: bool = False) -> tuple[dict[str, Any], Any]:
    from app.ingestion.scheduler import _build_worker_ingestion, _sync_source_async

    guardrails_engine.bind_repository(None)
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(
        return_value=IngestionJob(
            job_id="job-kb01", source_id="src-kb01", tenant_id=_TENANT, status="running",
            sync_mode="incremental",
        )
    )
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config())

    patches = _build_patches(store, MagicMock())
    for p in patches:
        p.start()
    try:
        _t, pipeline, _s = _build_worker_ingestion()
        assert isinstance(pipeline, IngestionPipeline)
        pipeline._quota = None  # no DB in this test; quota is covered elsewhere
        if fail_load:
            _FakeRepo.instances[-1].fail_load = True
        with (
            patch(
                "app.ingestion.scheduler._build_worker_ingestion",
                return_value=(tracker, pipeline, source_store),
            ),
            patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        ):
            result = await _sync_source_async(
                task=MagicMock(), source_id="src-kb01", tenant_id=_TENANT, triggered_by="scheduler"
            )
    finally:
        for p in reversed(patches):
            p.stop()
    return result, tracker


async def test_persisted_tenant_rule_blocks_a_worker_synced_document() -> None:
    store = _Store()
    result, _tracker = await _run_sync(store)
    assert result["docs_indexed"] == 0, result
    assert store.chunks == [], "a tenant-blocked document reached the knowledge store"


async def test_rules_load_failure_fails_the_worker_document_closed() -> None:
    store = _Store()
    result, tracker = await _run_sync(store, fail_load=True)
    assert result["docs_indexed"] == 0
    assert result["docs_failed"] == 1
    assert store.chunks == []
    tracker.add_to_dlq.assert_awaited_once()


async def test_production_screening_without_a_bound_repository_fails_closed() -> None:
    guardrails_engine.bind_repository(None)
    pipeline = IngestionPipeline()
    settings = MagicMock()
    settings.is_production = True
    with (
        patch("app.core.config.get_settings", return_value=settings),
        pytest.raises(IngestionScreeningUnavailableError),
    ):
        await pipeline.screen_text("harmless text", tenant_id=_TENANT, pii_action="allow")


async def test_non_production_screening_without_a_repository_still_runs() -> None:
    guardrails_engine.bind_repository(None)
    pipeline = IngestionPipeline()
    settings = MagicMock()
    settings.is_production = False
    with patch("app.core.config.get_settings", return_value=settings):
        result = await pipeline.screen_text("harmless text", tenant_id=_TENANT, pii_action="allow")
    assert result.blocked_reason == ""
