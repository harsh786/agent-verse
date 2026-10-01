"""KB-15: sync history, cancel, reindex, validate and DLQ-retry routes.

``app/api/ingestion.py``'s docstring advertised ``/sync/cancel``, ``/reindex``
and ``/sources/validate`` that did not exist, there was no DLQ retry, and
``/sync/status`` read only the API process's in-memory jobs (syncs run in
Celery workers, so it answered ``never_synced`` in production).
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import _run_sync, documents_router, router
from app.ingestion.base_connector import ConnectionHealth
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ops", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_ops_key"


@pytest.fixture(autouse=True)
def _clear_sources() -> Any:
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()


def _client(**state: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.include_router(documents_router)
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


_AUTH = {"X-API-Key": _KEY}


def _source(**overrides: Any) -> SourceConfig:
    values: dict[str, Any] = {
        "source_id": uuid.uuid4().hex,
        "tenant_id": _CTX.tenant_id,
        "name": "src",
        "family": SourceFamily.WEB,
        "source_type": "http",
        "collection_id": "col-1",
    }
    values.update(overrides)
    source = SourceConfig(**values)
    ingestion_mod._SOURCES[source.source_id] = source
    return source


# ── Docstring honesty ────────────────────────────────────────────────────────


def test_every_endpoint_in_the_module_docstring_exists() -> None:
    doc = ingestion_mod.__doc__ or ""
    routes = {
        (method, route.path)  # type: ignore[attr-defined]
        for r in (*router.routes, *documents_router.routes)
        for route in [r]
        for method in getattr(route, "methods", set())
    }
    advertised = re.findall(r"^\s+([A-Z/]+)\s+(/\S+)", doc, flags=re.MULTILINE)
    assert advertised, "docstring lists no endpoints"
    for methods, path in advertised:
        for method in methods.split("/"):
            assert (method, path) in routes, f"docstring advertises {method} {path}"


# ── History ──────────────────────────────────────────────────────────────────


async def test_sync_history_lists_jobs_newest_first() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    first = await tracker.create_job(source, job_id="job-a", triggered_by="manual")
    first.created_at = "2026-01-01T00:00:00+00:00"
    second = await tracker.create_job(source, job_id="job-b", triggered_by="scheduler")
    second.created_at = "2026-01-02T00:00:00+00:00"
    resp = _client(ingestion_job_tracker=tracker).get(
        f"/sources/{source.source_id}/sync/history", headers=_AUTH
    )
    assert resp.status_code == 200
    assert [j["job_id"] for j in resp.json()] == ["job-b", "job-a"]


def test_sync_status_reads_the_durable_job_history() -> None:
    source = _source()
    tracker = MagicMock()
    tracker.list_jobs = AsyncMock(return_value=[{"job_id": "worker-job", "status": "running"}])
    resp = _client(ingestion_job_tracker=tracker).get(
        f"/sources/{source.source_id}/sync/status", headers=_AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "worker-job"
    tracker.list_jobs.assert_awaited_once_with(source.source_id, _CTX.tenant_id, limit=1)


# ── Cancel ───────────────────────────────────────────────────────────────────


async def test_cancel_flags_the_running_sync() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    job_id = await tracker.acquire_lock(source.source_id, _CTX.tenant_id)
    resp = _client(ingestion_job_tracker=tracker).post(
        f"/sources/{source.source_id}/sync/cancel", headers=_AUTH
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "cancelling", "job_id": job_id}
    assert await tracker.is_cancel_requested(_CTX.tenant_id, str(job_id)) is True


def test_cancel_without_a_running_sync_is_409() -> None:
    source = _source()
    resp = _client(ingestion_job_tracker=IngestionJobTracker()).post(
        f"/sources/{source.source_id}/sync/cancel", headers=_AUTH
    )
    assert resp.status_code == 409


def test_cancel_unknown_source_is_404() -> None:
    resp = _client(ingestion_job_tracker=IngestionJobTracker()).post(
        "/sources/nope/sync/cancel", headers=_AUTH
    )
    assert resp.status_code == 404


async def test_api_side_sync_stops_when_cancelled() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    job_id = await tracker.acquire_lock(source.source_id, _CTX.tenant_id)
    assert job_id is not None
    ingested: list[str] = []

    class _Connector:
        async def get_delta(self, config: Any, cursor: Any) -> Any:
            for i in range(5):
                if i == 2:
                    await tracker.request_cancel(source.source_id, _CTX.tenant_id)
                yield (
                    RawDocument(doc_id=f"d{i}", source_id=source.source_id,
                                tenant_id=_CTX.tenant_id, content=b"x",
                                content_type="text/plain"),
                    f"cur-{i}",
                )

    pipeline = MagicMock()

    async def _ingest(raw_doc: RawDocument, config: Any) -> PipelineResult:
        ingested.append(raw_doc.doc_id)
        return PipelineResult(doc_id=raw_doc.doc_id, source_id=source.source_id, tenant_id=_CTX.tenant_id,
                              status="indexed", chunks_created=1)

    pipeline.ingest = _ingest
    with patch("app.ingestion.connector_registry.get_connector", return_value=_Connector):
        await _run_sync(source, pipeline, tracker, job_id)
    assert ingested == ["d0", "d1"]
    job = tracker.get_job(job_id)
    assert job is not None and job.status == "cancelled"
    assert await tracker.running_job_id(source.source_id, _CTX.tenant_id) is None


# ── Reindex ──────────────────────────────────────────────────────────────────


def test_reindex_queues_a_delete_and_full_resync() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        apply_async = task.apply_async
        resp = _client(ingestion_job_tracker=tracker).post(
            f"/sources/{source.source_id}/reindex", headers=_AUTH
        )
    assert resp.status_code == 202, resp.text
    kwargs = apply_async.call_args.kwargs
    assert kwargs["queue"] == "ingestion"
    assert kwargs["kwargs"]["reindex"] is True
    assert kwargs["kwargs"]["job_id"] == resp.json()["job_id"]
    assert kwargs["kwargs"]["triggered_by"] == "reindex"


async def test_reindex_while_syncing_is_409() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    await tracker.acquire_lock(source.source_id, _CTX.tenant_id)
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        apply_async = task.apply_async
        resp = _client(ingestion_job_tracker=tracker).post(
            f"/sources/{source.source_id}/reindex", headers=_AUTH
        )
    assert resp.status_code == 409
    apply_async.assert_not_called()


def test_reindex_of_a_held_collection_is_409_and_queues_nothing() -> None:
    """KB-43: a reindex deletes the Source's documents — never in a held collection."""
    tracker = IngestionJobTracker()
    source = _source()
    holds = MagicMock()
    holds.is_under_hold = AsyncMock(return_value=True)
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        resp = _client(ingestion_job_tracker=tracker, legal_hold_manager=holds).post(
            f"/sources/{source.source_id}/reindex", headers=_AUTH
        )
    assert resp.status_code == 409
    assert "legal hold" in resp.json()["detail"]
    task.apply_async.assert_not_called()
    holds.is_under_hold.assert_awaited_once_with(tenant_id=_CTX.tenant_id, resource_id="col-1")
    assert tracker._locks == {}


def test_reindex_with_unverifiable_holds_is_503() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    holds = MagicMock()
    holds.is_under_hold = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        resp = _client(ingestion_job_tracker=tracker, legal_hold_manager=holds).post(
            f"/sources/{source.source_id}/reindex", headers=_AUTH
        )
    assert resp.status_code == 503
    task.apply_async.assert_not_called()


def test_reindex_without_the_ingestion_framework_is_503() -> None:
    source = _source()
    resp = _client().post(f"/sources/{source.source_id}/reindex", headers=_AUTH)
    assert resp.status_code == 503


def test_reindex_enqueue_failure_releases_the_lock() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        task.apply_async.side_effect = RuntimeError("down")
        resp = _client(ingestion_job_tracker=tracker).post(
            f"/sources/{source.source_id}/reindex", headers=_AUTH
        )
    assert resp.status_code == 503
    assert tracker._locks == {}


# ── Validate ─────────────────────────────────────────────────────────────────


_BODY = {"name": "n", "family": "web", "source_type": "http", "connection_config": {}}


def test_validate_reports_an_unknown_family() -> None:
    resp = _client().post("/sources/validate", json={**_BODY, "family": "nope"}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert any("family" in e for e in resp.json()["errors"])


def test_validate_reports_an_unknown_source_type() -> None:
    with patch("app.ingestion.connector_registry.get_connector", side_effect=KeyError("x")):
        resp = _client().post("/sources/validate", json=_BODY, headers=_AUTH)
    assert resp.json()["valid"] is False
    assert any("source_type" in e for e in resp.json()["errors"])


def test_validate_checks_the_connection() -> None:
    class _Connector:
        async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
            assert config.tenant_id == _CTX.tenant_id
            return ConnectionHealth(ok=False, latency_ms=3.0, error="401 Unauthorized")

    with patch("app.ingestion.connector_registry.get_connector", return_value=_Connector):
        resp = _client().post("/sources/validate", json=_BODY, headers=_AUTH)
    body = resp.json()
    assert body["valid"] is False
    assert body["connection"]["ok"] is False
    assert "401" in body["connection"]["error"]
    assert ingestion_mod._SOURCES == {}  # nothing saved


def test_validate_ok_without_connection_check() -> None:
    class _Connector:
        async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
            raise AssertionError("must not connect")

    with patch("app.ingestion.connector_registry.get_connector", return_value=_Connector):
        resp = _client().post(
            "/sources/validate?check_connection=false", json=_BODY, headers=_AUTH
        )
    assert resp.json() == {"valid": True, "errors": [], "connection": None}


def test_validate_rejects_an_unsupported_chunking_strategy_as_422() -> None:
    resp = _client().post(
        "/sources/validate", json={**_BODY, "chunking_strategy": "bogus"}, headers=_AUTH
    )
    assert resp.status_code == 422


# ── DLQ retry ────────────────────────────────────────────────────────────────


def _dlq_tracker(entry: dict[str, Any] | None) -> Any:
    tracker = MagicMock()
    tracker._db = object()
    tracker.get_dlq_entry = AsyncMock(return_value=entry)
    return tracker


def test_dlq_retry_enqueues_the_entry() -> None:
    tracker = _dlq_tracker({"dlq_id": "d1", "resolved_at": None})
    with patch("app.ingestion.scheduler.retry_dlq_entry_task") as task:
        apply_async = task.apply_async
        resp = _client(ingestion_job_tracker=tracker).post(
            "/ingestion/dlq/d1/retry", headers=_AUTH
        )
    assert resp.status_code == 202, resp.text
    assert apply_async.call_args.kwargs["kwargs"] == {"dlq_id": "d1", "tenant_id": _CTX.tenant_id}
    assert apply_async.call_args.kwargs["queue"] == "ingestion"
    tracker.get_dlq_entry.assert_awaited_once_with("d1", _CTX.tenant_id)


def test_dlq_retry_of_an_unknown_entry_is_404() -> None:
    with patch("app.ingestion.scheduler.retry_dlq_entry_task") as task:
        apply_async = task.apply_async
        resp = _client(ingestion_job_tracker=_dlq_tracker(None)).post(
            "/ingestion/dlq/zz/retry", headers=_AUTH
        )
    assert resp.status_code == 404
    apply_async.assert_not_called()


def test_dlq_retry_of_a_resolved_entry_is_409() -> None:
    tracker = _dlq_tracker({"dlq_id": "d1", "resolved_at": "2026-01-01T00:00:00Z"})
    resp = _client(ingestion_job_tracker=tracker).post("/ingestion/dlq/d1/retry", headers=_AUTH)
    assert resp.status_code == 409


def test_dlq_retry_without_a_database_is_503() -> None:
    resp = _client(ingestion_job_tracker=IngestionJobTracker()).post(
        "/ingestion/dlq/d1/retry", headers=_AUTH
    )
    assert resp.status_code == 503
