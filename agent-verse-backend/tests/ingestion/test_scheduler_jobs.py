"""Tests for app.ingestion.scheduler — job dispatch/sync/retry logic.

_jitter / _backoff_seconds / BEAT_SCHEDULE are already covered by
tests/ingestion/test_new_connectors.py::TestIngestionScheduler. This file
covers the async job bodies (_sync_source_async, _dispatch_due_sources_async,
_retry_dlq_async) and their Celery task wrappers, with all DB/Redis/connector
dependencies mocked out.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.scheduler import (
    _dispatch_due_sources_async,
    _retry_dlq_async,
    _sync_source_async,
    dispatch_due_sources_task,
    retry_dlq_entries_task,
    sync_source_task,
)
from app.providers.embedder_factory import EmbedderResolution
from tests.ingestion._lease import install_lease
from app.ingestion.source_config import (
    IngestionJob,
    PipelineResult,
    RawDocument,
    SourceConfig,
    SourceFamily,
)


def _config(**overrides) -> SourceConfig:
    base = dict(
        source_id="src-1",
        tenant_id="t1",
        name="Test Source",
        family=SourceFamily.WEB,
        source_type="http",
        enabled=True,
        sync_mode="incremental",
        connection_config={},
        cursor_value="",
        consecutive_failures=0,
        last_synced_at=None,
        collection_id="col-1",
    )
    base.update(overrides)
    return SourceConfig(**base)


def _job(**overrides) -> IngestionJob:
    base = dict(
        job_id="job-1",
        source_id="src-1",
        tenant_id="t1",
        status="running",
        sync_mode="incremental",
    )
    base.update(overrides)
    return IngestionJob(**base)


def _worker_mocks(tracker=None, pipeline=None, source_store=None):
    tracker = install_lease(tracker or AsyncMock())  # TG-12: the worker holds a lease
    pipeline = pipeline or AsyncMock()
    source_store = source_store or AsyncMock()
    return patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, pipeline, source_store),
    )


class _FakeConnector:
    """A connector whose get_delta yields a fixed sequence of docs."""

    def __init__(self, docs):
        self._docs = docs

    async def get_delta(self, config, cursor):
        for doc, cur in self._docs:
            yield doc, cur


class _RaisingConnector:
    """A connector whose get_delta raises before yielding anything."""

    async def get_delta(self, config, cursor):
        if False:  # pragma: no cover - makes this a real async generator
            yield
        raise RuntimeError("connector exploded")


def _raw_doc(doc_id: str) -> RawDocument:
    return RawDocument(
        doc_id=doc_id, source_id="src-1", tenant_id="t1", content=b"x", content_type="text/plain"
    )


# ── _build_worker_ingestion ───────────────────────────────────────────────────


def test_build_worker_ingestion_wires_db_backed_services() -> None:
    from app.ingestion.scheduler import _build_worker_ingestion

    fake_db_factory = MagicMock()
    fake_system_factory = MagicMock()
    fake_provider = MagicMock()
    fake_provider._model_name = "all-mpnet-base-v2"
    fake_knowledge_store = MagicMock()
    fake_pipeline = MagicMock()
    fake_tracker = MagicMock()
    fake_source_store = MagicMock()

    fake_redis = MagicMock()
    with (
        patch("app.db.session.get_session_factory", return_value=fake_db_factory),
        patch("app.db.session.get_system_session_factory", return_value=fake_system_factory),
        patch("app.ingestion.scheduler._reconcile_redis", return_value=fake_redis),
        # The chat LLM must NOT embed documents: queries are embedded with the
        # configured query embedder, so documents must be too (same vector space).
        patch("app.providers.registry.resolve_provider", return_value=MagicMock()),
        patch(
            "app.providers.embedder_factory.resolve_embedder",
            return_value=EmbedderResolution(embedder=fake_provider, dimension=768),
        ),
        patch("app.rag.store.KnowledgeStore", return_value=fake_knowledge_store) as ks_cls,
        patch(
            "app.ingestion.pipeline.IngestionPipeline", return_value=fake_pipeline
        ) as pipeline_cls,
        patch(
            "app.ingestion.job_tracker.IngestionJobTracker", return_value=fake_tracker
        ) as tracker_cls,
        patch(
            "app.ingestion.source_store.SourceConfigStore", return_value=fake_source_store
        ) as store_cls,
    ):
        tracker, pipeline, source_store = _build_worker_ingestion()

    assert tracker is fake_tracker
    assert pipeline is fake_pipeline
    assert source_store is fake_source_store
    # New collections are sized to the resolved embedder's real width and
    # labelled with its real model (USR-3).
    ks_cls.assert_called_once_with(
        fake_db_factory, embedding_dim=768, embedder_name="all-mpnet-base-v2"
    )
    pipeline_cls.assert_called_once()
    kwargs = pipeline_cls.call_args.kwargs
    assert kwargs["knowledge_store"] is fake_knowledge_store
    # The embedder is traced (cost + spans) around the resolved provider.
    from app.observability.traced_provider import TracedProvider

    embedder = kwargs["embedder"]
    assert isinstance(embedder, TracedProvider)
    assert embedder._inner is fake_provider
    # Stage 6 PII + Stage 1 quota are wired (both were previously never passed).
    from app.ingestion.pii import RegexPIIAnalyzer
    from app.ingestion.quota import IngestionQuotaEnforcer

    assert isinstance(kwargs["pii_analyzer"], RegexPIIAnalyzer)
    assert isinstance(kwargs["quota_enforcer"], IngestionQuotaEnforcer)
    # Per-tenant work (job rows, cursors, the Source itself) runs on the
    # application factory under RLS; only the tracker's cross-tenant DLQ scan
    # gets the maintenance-role factory. The Source store gets no system access.
    # TG-12: and the shared Redis that holds the per-Source sync lock.
    tracker_cls.assert_called_once_with(
        db=fake_db_factory, system_db=fake_system_factory, redis=fake_redis
    )
    store_cls.assert_called_once_with(db=fake_db_factory)


# ── _sync_source_async ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_already_locked_skips() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=False)

    with _worker_mocks(tracker=tracker):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result == {"skipped": True, "reason": "already_running"}
    tracker.release_lock.assert_not_called()


@pytest.mark.asyncio
async def test_sync_source_not_found() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=None)

    with _worker_mocks(tracker=tracker, source_store=source_store):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result == {"error": "source_not_found"}
    tracker.release_lock.assert_called_once_with("src-1", "t1", True)
    # The worker knows the tenant: a tenant-scoped (RLS) read, not a system read.
    source_store.get.assert_awaited_once_with("src-1", "t1")


@pytest.mark.asyncio
async def test_sync_source_disabled() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config(enabled=False))

    with _worker_mocks(tracker=tracker, source_store=source_store):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result == {"skipped": True, "reason": "source_disabled"}


@pytest.mark.asyncio
async def test_sync_source_in_backoff() -> None:
    import datetime

    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    source_store = AsyncMock()
    # consecutive_failures=1 → backoff >= 60s; last_synced_at "now" → elapsed ~0.
    now = datetime.datetime.now(datetime.UTC).isoformat()
    source_store.get = AsyncMock(
        return_value=_config(consecutive_failures=1, last_synced_at=now)
    )

    with _worker_mocks(tracker=tracker, source_store=source_store):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result["skipped"] is True
    assert result["reason"] == "backoff"
    assert result["retry_in_seconds"] > 0


@pytest.mark.asyncio
async def test_sync_no_connector_registered() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config(source_type="no_such_type"))

    with (
        _worker_mocks(tracker=tracker, source_store=source_store),
        patch(
            "app.ingestion.connector_registry.get_connector",
            side_effect=KeyError("No connector registered for source_type='no_such_type'"),
        ),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result == {"error": "No connector registered for source_type='no_such_type'"}
    tracker.complete_job.assert_awaited_once()
    tracker.release_lock.assert_awaited_once_with("src-1", "t1", True)


@pytest.mark.asyncio
async def test_sync_success_counts_indexed_skipped_failed_and_dlq() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(return_value=_job())

    source_store = AsyncMock()
    cfg = _config(cursor_value="")
    source_store.get = AsyncMock(return_value=cfg)

    pipeline = AsyncMock()
    pipeline.ingest = AsyncMock(
        side_effect=[
            PipelineResult(doc_id="d1", source_id="src-1", tenant_id="t1", status="indexed"),
            PipelineResult(doc_id="d2", source_id="src-1", tenant_id="t1", status="skipped"),
            PipelineResult(
                doc_id="d3", source_id="src-1", tenant_id="t1", status="failed", error="boom"
            ),
        ]
    )

    docs = [(_raw_doc("d1"), "c1"), (_raw_doc("d2"), "c2"), (_raw_doc("d3"), "c3")]
    connector_cls = lambda: _FakeConnector(docs)  # noqa: E731

    with (
        _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result["docs_indexed"] == 1
    assert result["docs_skipped"] == 1
    assert result["docs_failed"] == 1
    tracker.add_to_dlq.assert_called_once()
    dlq_kwargs = tracker.add_to_dlq.call_args.kwargs
    assert dlq_kwargs["doc_id"] == "d3"
    assert dlq_kwargs["error"] == "boom"
    assert dlq_kwargs["job_id"] == "job-1"
    tracker.update_cursor.assert_called_once_with(
        tracker.create_job.return_value, "c3", cfg, fence=1  # fenced (TG-12)
    )
    tracker.complete_job.assert_called_once()
    # mark_synced alone owns consecutive_failures (the tracker counter calls
    # would double-count against the backoff).
    tracker.reset_failure_counter.assert_not_called()
    source_store.mark_synced.assert_called_once_with(
        "src-1", "t1", docs_indexed=1, chunks=0, failed=1
    )
    source_store.update.assert_called_once_with("src-1", "t1", cursor_value="c3")
    tracker.release_lock.assert_called_once_with("src-1", "t1", True)


@pytest.mark.asyncio
async def test_sync_commits_cursor_every_100_docs() -> None:
    """Cursor is committed mid-loop every 100 docs (LAW-14 atomicity), not just
    at the very end — exercised here with exactly 100 successful docs."""
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(return_value=_job())

    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config())

    pipeline = AsyncMock()
    pipeline.ingest = AsyncMock(
        return_value=PipelineResult(doc_id="d", source_id="src-1", tenant_id="t1", status="indexed")
    )

    docs = [(_raw_doc(f"d{i}"), f"c{i}") for i in range(100)]
    connector_cls = lambda: _FakeConnector(docs)  # noqa: E731

    with (
        _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result["docs_indexed"] == 100
    # One mid-loop commit (at doc #100) plus the final commit after the loop.
    assert tracker.update_cursor.call_count == 2
    assert tracker.update_cursor.call_args_list[0].args[1] == "c99"


@pytest.mark.asyncio
async def test_sync_per_doc_exception_is_counted_and_queued_for_retry() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(return_value=_job())

    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config())

    pipeline = AsyncMock()
    pipeline.ingest = AsyncMock(side_effect=RuntimeError("pipeline blew up"))

    docs = [(_raw_doc("d1"), "c1")]
    connector_cls = lambda: _FakeConnector(docs)  # noqa: E731

    with (
        _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"
        )

    assert result["docs_failed"] == 1
    assert result["docs_indexed"] == 0
    # USR-4: an unexpected per-document error goes to the DLQ (it used to be lost).
    tracker.add_to_dlq.assert_awaited_once()
    assert tracker.add_to_dlq.await_args.kwargs["doc_id"] == "d1"
    tracker.complete_job.assert_called_once()


@pytest.mark.asyncio
async def test_sync_outer_exception_retries_and_releases_lock() -> None:
    tracker = AsyncMock()
    tracker.acquire_lock = AsyncMock(return_value=True)
    tracker.create_job = AsyncMock(return_value=_job())

    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=_config())

    pipeline = AsyncMock()
    connector_cls = _RaisingConnector

    task = MagicMock()
    task.retry = MagicMock(return_value=RuntimeError("celery retry"))

    with (
        _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls),
    ):
        with pytest.raises(RuntimeError, match="celery retry"):
            await _sync_source_async(
                task=task, source_id="src-1", tenant_id="t1", triggered_by="scheduler"
            )

    tracker.complete_job.assert_called_once()
    assert tracker.complete_job.call_args.kwargs["error"]
    # Exactly one failure is counted — by mark_synced, not also by the tracker.
    tracker.increment_failure_counter.assert_not_called()
    source_store.mark_synced.assert_called_once_with(
        "src-1", "t1", docs_indexed=0, chunks=0, failed=1
    )
    tracker.release_lock.assert_called_once_with("src-1", "t1", True)
    task.retry.assert_called_once()


# ── _dispatch_due_sources_async ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_due_sources_enqueues_each_source() -> None:
    source_store = AsyncMock()
    source_store.list_due = AsyncMock(return_value=[("src-1", "t1"), ("src-2", "t2")])
    fake_system_factory = MagicMock()

    with (
        patch("app.db.session.get_system_session_factory", return_value=fake_system_factory),
        patch(
            "app.ingestion.source_store.SourceConfigStore", return_value=source_store
        ) as store_cls,
        patch.object(sync_source_task, "apply_async") as mock_apply_async,
    ):
        result = await _dispatch_due_sources_async()

    assert result["dispatched"] == 2
    assert mock_apply_async.call_count == 2
    kwargs_calls = [c.kwargs["kwargs"] for c in mock_apply_async.call_args_list]
    assert {"src-1", "src-2"} == {k["source_id"] for k in kwargs_calls}
    assert {("src-1", "t1"), ("src-2", "t2")} == {
        (k["source_id"], k["tenant_id"]) for k in kwargs_calls
    }
    # The cross-tenant due-scan runs on the maintenance role ONLY — never on the
    # application's NOBYPASSRLS factory, where system_session makes it fail.
    store_cls.assert_called_once_with(system_db=fake_system_factory)


@pytest.mark.asyncio
async def test_dispatch_due_sources_none_due() -> None:
    source_store = AsyncMock()
    source_store.list_due = AsyncMock(return_value=[])

    with (
        patch("app.db.session.get_system_session_factory", return_value=MagicMock()),
        patch("app.ingestion.source_store.SourceConfigStore", return_value=source_store),
        patch.object(sync_source_task, "apply_async") as mock_apply_async,
    ):
        result = await _dispatch_due_sources_async()

    assert result["dispatched"] == 0
    mock_apply_async.assert_not_called()


# ── _retry_dlq_async ──────────────────────────────────────────────────────────


def _dlq_json(doc_id: str) -> str:
    """A raw_doc_json exactly as IngestionJobTracker.add_to_dlq writes it."""
    import dataclasses
    import json

    from app.ingestion.job_tracker import _json_default

    return json.dumps(dataclasses.asdict(_raw_doc(doc_id)), default=_json_default)


def _dlq_entry(dlq_id, retry_count=0, tenant_id="t1", source_id="src-1", raw_doc_json=None):
    """A row as get_retryable_dlq_entries returns it: a plain dict."""
    return {
        "dlq_id": dlq_id,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "doc_id": dlq_id,
        "job_id": None,
        "error_message": "boom",
        "raw_doc_json": _dlq_json(dlq_id) if raw_doc_json is None else raw_doc_json,
        "retry_count": retry_count,
    }


def _retry_mocks(entries, pipeline=None, source_store=None):
    tracker = AsyncMock()
    tracker.get_retryable_dlq_entries = AsyncMock(return_value=entries)
    pipeline = pipeline or AsyncMock()
    if source_store is None:
        source_store = AsyncMock()
        source_store.get = AsyncMock(return_value=_config())
    return tracker, pipeline, source_store


@pytest.mark.asyncio
async def test_retry_dlq_uses_the_db_backed_worker_services() -> None:
    # It built IngestionJobTracker() with no DB (scan always []) and an
    # IngestionPipeline() with no knowledge store/embedder.
    tracker, pipeline, source_store = _retry_mocks([])
    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store) as build:
        result = await _retry_dlq_async()
    build.assert_called_once_with()
    tracker.get_retryable_dlq_entries.assert_awaited_once_with(max_entries=50)
    assert result == {"retried": 0, "succeeded": 0, "still_failed": 0}


@pytest.mark.asyncio
async def test_retry_dlq_permanent_failure_over_max_retries() -> None:
    tracker, pipeline, source_store = _retry_mocks([_dlq_entry("e1", retry_count=5)])

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    tracker.mark_dlq_permanent_failure.assert_called_once_with("e1", "t1")
    assert result == {"retried": 0, "succeeded": 0, "still_failed": 0}
    pipeline.run.assert_not_called()


@pytest.mark.asyncio
async def test_retry_dlq_success_resolves_entry() -> None:
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(
        return_value=PipelineResult(doc_id="e2", source_id="s", tenant_id="t1", status="indexed")
    )
    cfg = _config()
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=cfg)
    tracker, pipeline, source_store = _retry_mocks(
        [_dlq_entry("e2", retry_count=1)], pipeline=pipeline, source_store=source_store
    )

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    tracker.resolve_dlq_entry.assert_called_once_with("e2", "t1")
    assert result == {"retried": 1, "succeeded": 1, "still_failed": 0}
    # The entry's Source is loaded for ITS tenant (RLS), and the replayed
    # document is the one that was dead-lettered, with its bytes intact.
    source_store.get.assert_awaited_once_with("src-1", "t1")
    replayed = pipeline.run.await_args.args[0]
    assert replayed == _raw_doc("e2")
    assert pipeline.run.await_args.kwargs["source_config"] is cfg


@pytest.mark.asyncio
async def test_retry_dlq_still_failing_increments_retry_count() -> None:
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(
        return_value=PipelineResult(
            doc_id="e3", source_id="s", tenant_id="t1", status="failed", error="still bad"
        )
    )
    tracker, pipeline, source_store = _retry_mocks(
        [_dlq_entry("e3", retry_count=2)], pipeline=pipeline
    )

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    tracker.increment_dlq_retry.assert_called_once_with("e3", "t1", error="still bad")
    assert result == {"retried": 1, "succeeded": 0, "still_failed": 1}


@pytest.mark.asyncio
async def test_retry_dlq_skip_reason_is_recorded_as_the_error() -> None:
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(
        return_value=PipelineResult(
            doc_id="e5", source_id="s", tenant_id="t1", status="skipped", skip_reason="quality"
        )
    )
    tracker, pipeline, source_store = _retry_mocks([_dlq_entry("e5")], pipeline=pipeline)

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        await _retry_dlq_async()

    tracker.increment_dlq_retry.assert_called_once_with("e5", "t1", error="quality")


@pytest.mark.asyncio
async def test_retry_dlq_exception_counts_as_still_failed() -> None:
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(side_effect=RuntimeError("kaboom"))
    tracker, pipeline, source_store = _retry_mocks(
        [_dlq_entry("e4", retry_count=0)], pipeline=pipeline
    )

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    tracker.increment_dlq_retry.assert_called_once_with("e4", "t1", error="kaboom")
    assert result["still_failed"] == 1


@pytest.mark.asyncio
async def test_retry_dlq_unreplayable_payload_is_flagged_permanent() -> None:
    # e.g. a repo-ingest DLQ entry: parameters, not a connector document.
    import json

    repo_payload = json.dumps({"kind": "repository", "repo_url": "https://x/y"})
    pipeline = AsyncMock()
    tracker, pipeline, source_store = _retry_mocks(
        [_dlq_entry("e6", raw_doc_json=repo_payload)], pipeline=pipeline
    )

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    tracker.mark_dlq_permanent_failure.assert_called_once_with("e6", "t1")
    pipeline.run.assert_not_called()
    assert result == {"retried": 0, "succeeded": 0, "still_failed": 0}


@pytest.mark.asyncio
async def test_retry_dlq_skips_rows_without_identity() -> None:
    tracker, pipeline, source_store = _retry_mocks([_dlq_entry("e7", tenant_id="")])

    with _worker_mocks(tracker=tracker, pipeline=pipeline, source_store=source_store):
        result = await _retry_dlq_async()

    pipeline.run.assert_not_called()
    tracker.mark_dlq_permanent_failure.assert_not_called()
    assert result == {"retried": 0, "succeeded": 0, "still_failed": 0}


# ── Celery task wrappers ──────────────────────────────────────────────────────


@pytest.fixture
def _fresh_event_loop():
    """asyncio.get_event_loop() needs a current loop; pytest-asyncio closes its
    per-test loop on teardown, so plain (non-async) tests calling into these
    sync Celery wrappers must set up their own."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_sync_source_task_delegates_to_async_body(_fresh_event_loop) -> None:
    with patch(
        "app.ingestion.scheduler._sync_source_async",
        new=AsyncMock(return_value={"ok": True}),
    ) as mock_async:
        result = sync_source_task(source_id="src-1", tenant_id="t1", triggered_by="manual")

    assert result == {"ok": True}
    mock_async.assert_called_once()
    assert mock_async.call_args.kwargs["source_id"] == "src-1"
    assert mock_async.call_args.kwargs["triggered_by"] == "manual"


def test_dispatch_due_sources_task_delegates(_fresh_event_loop) -> None:
    with patch(
        "app.ingestion.scheduler._dispatch_due_sources_async",
        new=AsyncMock(return_value={"dispatched": 3}),
    ):
        result = dispatch_due_sources_task()
    assert result == {"dispatched": 3}


def test_retry_dlq_entries_task_delegates(_fresh_event_loop) -> None:
    with patch(
        "app.ingestion.scheduler._retry_dlq_async",
        new=AsyncMock(return_value={"retried": 2}),
    ):
        result = retry_dlq_entries_task()
    assert result == {"retried": 2}
