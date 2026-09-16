"""Tests for app/api/ingestion.py — source CRUD, sync control, catalogue,
preview, stats, and the documents/quota/cost/DLQ stubs.

Follows the lightweight FastAPI + TenantMiddleware pattern used by
tests/api/test_connectors.py: a minimal app with only the routers under test,
a fake key resolver, and X-API-Key auth headers.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import _run_sync, documents_router, router
from app.ingestion.base_connector import ConnectionHealth
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ing", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_professional_ingest"


@pytest.fixture(autouse=True)
def _clear_sources():
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()


def _make_app(**state: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)
    app.include_router(documents_router)
    for key, value in state.items():
        setattr(app.state, key, value)
    return app


def _client(**state: Any) -> TestClient:
    return TestClient(_make_app(**state), raise_server_exceptions=False)


def _auth() -> dict[str, str]:
    return {"X-API-Key": _VALID_KEY}


def _make_source(**overrides: Any) -> SourceConfig:
    defaults: dict[str, Any] = dict(
        source_id=uuid.uuid4().hex,
        tenant_id=_CTX.tenant_id,
        name="test-src",
        family=SourceFamily.CODE_REPOSITORY,
        source_type="github",
    )
    defaults.update(overrides)
    return SourceConfig(**defaults)


# ── Auth ───────────────────────────────────────────────────────────────────────


def test_require_tenant_401_without_auth_context() -> None:
    """No TenantMiddleware wired at all -> request.state.tenant is unset -> 401."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/sources")
    assert resp.status_code == 401


# ── Sources CRUD (in-memory, no source_store) ──────────────────────────────────


def test_create_source_unknown_family_returns_422() -> None:
    client = _client()
    resp = client.post(
        "/sources",
        json={"name": "s1", "family": "not_a_real_family", "source_type": "github"},
        headers=_auth(),
    )
    assert resp.status_code == 422
    assert "Unknown family" in resp.json()["detail"]


def test_create_list_get_source_in_memory() -> None:
    client = _client()
    resp = client.post(
        "/sources",
        json={"name": "s1", "family": "code_repository", "source_type": "github"},
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "s1"
    assert body["family"] == "code_repository"
    source_id = body["source_id"]

    resp = client.get("/sources", headers=_auth())
    assert resp.status_code == 200
    assert any(s["source_id"] == source_id for s in resp.json())

    resp = client.get(f"/sources/{source_id}", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["source_id"] == source_id


def test_get_source_404_in_memory() -> None:
    client = _client()
    resp = client.get("/sources/does-not-exist", headers=_auth())
    assert resp.status_code == 404


def test_update_source_404_in_memory() -> None:
    client = _client()
    resp = client.patch("/sources/nope", json={"name": "renamed"}, headers=_auth())
    assert resp.status_code == 404


def test_update_source_in_memory_partial_update() -> None:
    client = _client()
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    resp = client.patch(
        f"/sources/{source.source_id}",
        json={"name": "renamed", "enabled": False},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["enabled"] is False
    # Untouched field preserved.
    assert body["source_type"] == "github"


def test_delete_source_404_in_memory() -> None:
    client = _client()
    resp = client.delete("/sources/nope", headers=_auth())
    assert resp.status_code == 404


def test_delete_source_in_memory_success() -> None:
    client = _client()
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    resp = client.delete(f"/sources/{source.source_id}", headers=_auth())
    assert resp.status_code == 204
    assert source.source_id not in ingestion_mod._SOURCES

    resp = client.get(f"/sources/{source.source_id}", headers=_auth())
    assert resp.status_code == 404


# ── Sources CRUD (with a DB-backed source_store double) ────────────────────────


def test_list_sources_with_store() -> None:
    store = AsyncMock()
    store.list.return_value = [_make_source()]
    client = _client(ingestion_source_store=store)
    resp = client.get("/sources", headers=_auth())
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    store.list.assert_awaited_once_with(_CTX.tenant_id)


def test_create_source_with_store() -> None:
    store = AsyncMock()
    client = _client(ingestion_source_store=store)
    resp = client.post(
        "/sources",
        json={"name": "s1", "family": "web", "source_type": "url_crawl"},
        headers=_auth(),
    )
    assert resp.status_code == 201
    store.create.assert_awaited_once()
    created_config = store.create.await_args.args[0]
    assert isinstance(created_config, SourceConfig)
    assert created_config.tenant_id == _CTX.tenant_id


def test_get_source_with_store_404() -> None:
    store = AsyncMock()
    store.get.return_value = None
    client = _client(ingestion_source_store=store)
    resp = client.get("/sources/missing", headers=_auth())
    assert resp.status_code == 404


def test_update_source_with_store_uses_updated_result() -> None:
    store = AsyncMock()
    original = _make_source()
    updated = _make_source(source_id=original.source_id, name="updated-name")
    store.get.return_value = original
    store.update.return_value = updated
    client = _client(ingestion_source_store=store)

    resp = client.patch(
        f"/sources/{original.source_id}", json={"name": "updated-name"}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"


def test_update_source_with_store_falls_back_to_original_when_update_returns_none() -> None:
    store = AsyncMock()
    original = _make_source()
    store.get.return_value = original
    store.update.return_value = None
    client = _client(ingestion_source_store=store)

    resp = client.patch(
        f"/sources/{original.source_id}", json={"name": "whatever"}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["source_id"] == original.source_id


def test_delete_source_with_store_404() -> None:
    store = AsyncMock()
    store.delete.return_value = False
    client = _client(ingestion_source_store=store)
    resp = client.delete("/sources/missing", headers=_auth())
    assert resp.status_code == 404


def test_delete_source_with_store_success() -> None:
    store = AsyncMock()
    store.delete.return_value = True
    client = _client(ingestion_source_store=store)
    resp = client.delete("/sources/some-id", headers=_auth())
    assert resp.status_code == 204


# ── Health check ─────────────────────────────────────────────────────────────────


def test_health_check_404_missing_source() -> None:
    client = _client()
    resp = client.get("/sources/missing/health", headers=_auth())
    assert resp.status_code == 404


def test_health_check_success() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    class _FakeConnector:
        async def validate_connection(self, src: SourceConfig) -> ConnectionHealth:
            return ConnectionHealth(ok=True, latency_ms=12.3, metadata={"repo_count": 4})

    client = _client()
    with patch(
        "app.ingestion.connector_registry.get_connector", return_value=_FakeConnector
    ):
        resp = client.get(f"/sources/{source.source_id}/health", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["latency_ms"] == 12.3
    assert body["metadata"] == {"repo_count": 4}


def test_health_check_unregistered_connector_type() -> None:
    source = _make_source(source_type="totally-unknown")
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client()
    with patch(
        "app.ingestion.connector_registry.get_connector",
        side_effect=KeyError("no connector"),
    ):
        resp = client.get(f"/sources/{source.source_id}/health", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "No connector" in body["error"]


def test_health_check_connector_raises_generic_exception() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    class _BrokenConnector:
        async def validate_connection(self, src: SourceConfig) -> ConnectionHealth:
            raise RuntimeError("connection refused")

    client = _client()
    with patch(
        "app.ingestion.connector_registry.get_connector", return_value=_BrokenConnector
    ):
        resp = client.get(f"/sources/{source.source_id}/health", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]


# ── Sync control ─────────────────────────────────────────────────────────────────


def test_trigger_sync_404_missing_source() -> None:
    client = _client()
    resp = client.post("/sources/missing/sync", headers=_auth())
    assert resp.status_code == 404


def test_trigger_sync_no_tracker_configured() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client()
    resp = client.post(f"/sources/{source.source_id}/sync", headers=_auth())
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"


def test_trigger_sync_already_running() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = None
    client = _client(ingestion_job_tracker=tracker)
    resp = client.post(f"/sources/{source.source_id}/sync", headers=_auth())
    assert resp.status_code == 202
    assert resp.json()["status"] == "already_running"


def test_trigger_sync_queues_background_task() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = "job-123"
    pipeline = AsyncMock()
    client = _client(ingestion_job_tracker=tracker, ingestion_pipeline=pipeline)

    with patch("app.api.ingestion._run_sync", new=AsyncMock()) as run_sync_mock:
        resp = client.post(f"/sources/{source.source_id}/sync", headers=_auth())

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"] == "job-123"
    run_sync_mock.assert_awaited_once()
    call_args = run_sync_mock.await_args.args
    assert call_args[0] is source
    assert call_args[3] == "job-123"


def test_sync_status_404_missing_source() -> None:
    client = _client()
    resp = client.get("/sources/missing/sync/status", headers=_auth())
    assert resp.status_code == 404


def test_sync_status_no_tracker() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client()
    resp = client.get(f"/sources/{source.source_id}/sync/status", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"status": "unknown"}


def test_sync_status_never_synced() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = IngestionJobTracker()
    client = _client(ingestion_job_tracker=tracker)
    resp = client.get(f"/sources/{source.source_id}/sync/status", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"status": "never_synced"}


async def test_sync_status_returns_latest_job() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = IngestionJobTracker()
    await tracker.create_job(source, job_id="job-older", triggered_by="manual")
    older = tracker.get_job("job-older")
    older.created_at = "2020-01-01T00:00:00+00:00"
    await tracker.create_job(source, job_id="job-newer", triggered_by="manual")
    newer = tracker.get_job("job-newer")
    newer.created_at = "2030-01-01T00:00:00+00:00"

    client = _client(ingestion_job_tracker=tracker)
    resp = client.get(f"/sources/{source.source_id}/sync/status", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-newer"


# ── Catalogue ────────────────────────────────────────────────────────────────────


def test_get_catalogue() -> None:
    client = _client()
    fake_metadata = [{"source_type": "github", "class": "GitHubConnector"}]
    with patch(
        "app.ingestion.connector_registry.get_connector_metadata",
        return_value=fake_metadata,
    ):
        resp = client.get("/sources/catalogue", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == fake_metadata


# ── Preview ──────────────────────────────────────────────────────────────────────


def test_preview_source_404_missing_source() -> None:
    client = _client()
    resp = client.post("/sources/missing/preview", headers=_auth())
    assert resp.status_code == 404


def test_preview_source_no_pipeline_configured() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client()
    resp = client.post(f"/sources/{source.source_id}/preview", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"error": "Ingestion pipeline not configured"}


def test_preview_source_success_stops_at_five_docs() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    class _FakePipeline:
        def __init__(self) -> None:
            self._dry_run = False

        async def ingest(self, raw_doc: Any, src: SourceConfig) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(
                doc_id=raw_doc, status="indexed", chunks_created=2, tokens_consumed=50
            )

    class _FakeConnector:
        async def get_delta(self, src: SourceConfig, cursor: str | None):
            for i in range(7):
                yield f"doc-{i}", f"cursor-{i}"

    pipeline = _FakePipeline()
    client = _client(ingestion_pipeline=pipeline)
    with patch(
        "app.ingestion.connector_registry.get_connector", return_value=_FakeConnector
    ):
        resp = client.post(f"/sources/{source.source_id}/preview", headers=_auth())

    assert resp.status_code == 200
    body = resp.json()
    assert body["docs_previewed"] == 5
    assert len(body["sample"]) == 5
    assert body["sample"][0]["chunks_would_create"] == 2
    assert body["sample"][0]["tokens_estimate"] == 50
    assert pipeline._dry_run is False  # reset in `finally`


def test_preview_source_connector_error_reports_partial_progress() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source

    class _FakePipeline:
        def __init__(self) -> None:
            self._dry_run = False

        async def ingest(self, raw_doc: Any, src: SourceConfig) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(
                doc_id=raw_doc, status="indexed", chunks_created=1, tokens_consumed=10
            )

    class _FailingConnector:
        async def get_delta(self, src: SourceConfig, cursor: str | None):
            yield "doc-0", "cursor-0"
            raise RuntimeError("source unreachable")

    pipeline = _FakePipeline()
    client = _client(ingestion_pipeline=pipeline)
    with patch(
        "app.ingestion.connector_registry.get_connector", return_value=_FailingConnector
    ):
        resp = client.post(f"/sources/{source.source_id}/preview", headers=_auth())

    assert resp.status_code == 200
    body = resp.json()
    assert body["docs_previewed"] == 1
    assert "source unreachable" in body["error"]
    assert pipeline._dry_run is False


# ── Stats ────────────────────────────────────────────────────────────────────────


def test_source_stats_404() -> None:
    client = _client()
    resp = client.get("/sources/missing/stats", headers=_auth())
    assert resp.status_code == 404


def test_source_stats_success() -> None:
    source = _make_source(total_docs_indexed=10, total_chunks=40, cursor_value="c-9")
    ingestion_mod._SOURCES[source.source_id] = source
    client = _client()
    resp = client.get(f"/sources/{source.source_id}/stats", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_docs_indexed"] == 10
    assert body["total_chunks"] == 40
    assert body["cursor_value"] == "c-9"


# ── Documents / quota / cost / DLQ stubs ────────────────────────────────────────


def test_list_documents_stub_always_empty() -> None:
    client = _client()
    resp = client.get("/ingestion/documents", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_quota_free_plan_default_limit() -> None:
    ctx = TenantContext(tenant_id="free-tenant", plan=PlanTier.FREE, api_key_id="k")

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "free-key" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(documents_router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/ingestion/quota", headers={"X-API-Key": "free-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "free"
    assert body["sources_limit"] == 2


def test_get_quota_unknown_plan_falls_back_to_unlimited() -> None:
    ctx = TenantContext(tenant_id="ent-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k")

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "ent-key" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(documents_router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/ingestion/quota", headers={"X-API-Key": "ent-key"})
    assert resp.status_code == 200
    assert resp.json()["sources_limit"] == 999_999


def test_get_quota_counts_only_current_tenants_sources() -> None:
    other = _make_source(tenant_id="some-other-tenant")
    mine = _make_source(tenant_id=_CTX.tenant_id)
    ingestion_mod._SOURCES[other.source_id] = other
    ingestion_mod._SOURCES[mine.source_id] = mine
    client = _client()
    resp = client.get("/ingestion/quota", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["sources_used"] == 1


def test_get_cost_stub() -> None:
    client = _client()
    resp = client.get("/ingestion/cost", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _CTX.tenant_id
    assert body["cost_usd_month"] == 0.0


def test_list_dlq_stub_always_empty() -> None:
    client = _client()
    resp = client.get("/ingestion/dlq", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == []


# ── _run_sync (background task) ─────────────────────────────────────────────────


class _FakePipeline:
    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self._dry_run = False
        self.calls: list[Any] = []

    async def ingest(self, raw_doc: Any, src: SourceConfig) -> Any:
        self.calls.append(raw_doc)
        return self._results.pop(0)


def _pipeline_result(status: str, chunks: int = 1, tokens: int = 5) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(status=status, chunks_created=chunks, tokens_consumed=tokens)


def _make_connector_cls(items: list[tuple[str, str]]):
    class _Connector:
        async def get_delta(self, src: SourceConfig, cursor: str | None):
            for raw_doc, new_cursor in items:
                yield raw_doc, new_cursor

    return _Connector


async def test_run_sync_happy_path_updates_job_and_source_store() -> None:
    source = _make_source(cursor_value="")
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock(source.source_id, source.tenant_id)
    pipeline = _FakePipeline(
        [
            _pipeline_result("indexed", chunks=2, tokens=10),
            _pipeline_result("skipped"),
            _pipeline_result("failed"),
        ]
    )
    source_store = AsyncMock()
    connector_cls = _make_connector_cls(
        [("doc-1", "c1"), ("doc-2", "c2"), ("doc-3", "c3")]
    )

    with patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls):
        await _run_sync(source, pipeline, tracker, job_id, source_store)

    job = tracker.get_job(job_id)
    assert job.status == "completed"
    assert job.docs_indexed == 1
    assert job.docs_skipped == 1
    assert job.docs_failed == 1
    # chunks/tokens accumulate for every result regardless of status:
    # indexed(chunks=2) + skipped(chunks=1, default) + failed(chunks=1, default) = 4
    assert job.chunks_created == 4
    assert job.cursor_after == "c3"

    source_store.mark_synced.assert_awaited_once()
    mark_kwargs = source_store.mark_synced.await_args.kwargs
    assert mark_kwargs["docs_indexed"] == 1
    assert mark_kwargs["chunks"] == 4
    assert mark_kwargs["failed"] == 1
    source_store.update.assert_awaited_once_with(
        source.source_id, source.tenant_id, cursor_value="c3"
    )
    # Lock released.
    assert tracker._locks.get(source.source_id) is None


async def test_run_sync_without_pipeline_does_not_index() -> None:
    source = _make_source()
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock(source.source_id, source.tenant_id)
    connector_cls = _make_connector_cls([("doc-1", "c1")])

    with patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls):
        await _run_sync(source, None, tracker, job_id, None)

    job = tracker.get_job(job_id)
    assert job.status == "completed"
    assert job.docs_indexed == 0
    assert job.cursor_after == ""  # never updated: only touched inside `if pipeline is not None`


async def test_run_sync_connector_error_marks_job_failed_and_releases_lock() -> None:
    source = _make_source()
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock(source.source_id, source.tenant_id)
    pipeline = _FakePipeline([_pipeline_result("indexed")])

    class _FailingConnector:
        async def get_delta(self, src: SourceConfig, cursor: str | None):
            yield "doc-1", "c1"
            raise RuntimeError("upstream down")

    source_store = AsyncMock()
    with patch(
        "app.ingestion.connector_registry.get_connector", return_value=_FailingConnector
    ):
        await _run_sync(source, pipeline, tracker, job_id, source_store)

    job = tracker.get_job(job_id)
    assert job.status == "failed"
    assert "upstream down" in job.error_message
    assert tracker._locks.get(source.source_id) is None
    source_store.mark_synced.assert_awaited_once()


async def test_run_sync_source_store_stats_failure_is_swallowed() -> None:
    source = _make_source()
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock(source.source_id, source.tenant_id)
    pipeline = _FakePipeline([_pipeline_result("indexed")])
    connector_cls = _make_connector_cls([("doc-1", "c1")])

    source_store = AsyncMock()
    source_store.mark_synced.side_effect = RuntimeError("db unreachable")

    with patch("app.ingestion.connector_registry.get_connector", return_value=connector_cls):
        # Must not raise even though mark_synced() blows up.
        await _run_sync(source, pipeline, tracker, job_id, source_store)

    job = tracker.get_job(job_id)
    assert job.status == "completed"
