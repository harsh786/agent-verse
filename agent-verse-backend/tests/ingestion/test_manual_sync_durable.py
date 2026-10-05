"""KB-16: a manual source sync runs as a durable Celery job, not in the API process.

``POST /sources/{id}/sync`` ran the sync in FastAPI ``BackgroundTasks``: a
restart or scale-down lost it with the job left ``running`` and the lock held.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.api import ingestion as ingestion_mod
from app.ingestion.source_config import IngestionJob, PipelineResult
from tests.ingestion._lease import install_lease
from tests.api.test_ingestion_api import _auth, _client, _make_source


def test_manual_sync_enqueues_the_celery_sync_task() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = "job-123"
    client: TestClient = _client(ingestion_job_tracker=tracker, ingestion_pipeline=AsyncMock())

    with (
        patch("app.ingestion.scheduler.sync_source_task") as task,
        patch("app.api.ingestion._run_sync", new=AsyncMock()) as in_process,
    ):
        resp = client.post(f"/sources/{source.source_id}/sync", headers=_auth())

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "queued", "job_id": "job-123"}
    in_process.assert_not_awaited()
    enqueue = task.apply_async
    enqueue.assert_called_once()
    kwargs = enqueue.call_args.kwargs
    assert kwargs["kwargs"] == {
        "source_id": source.source_id,
        "tenant_id": source.tenant_id,
        "triggered_by": "manual",
        "job_id": "job-123",
    }
    assert kwargs["queue"] == "ingestion"
    tracker.release_lock.assert_not_awaited()


def test_a_failed_enqueue_is_503_and_releases_the_lock() -> None:
    source = _make_source()
    ingestion_mod._SOURCES[source.source_id] = source
    tracker = AsyncMock()
    tracker.acquire_lock.return_value = "job-9"
    client = _client(ingestion_job_tracker=tracker, ingestion_pipeline=AsyncMock())

    with patch("app.ingestion.scheduler.sync_source_task") as task:
        task.apply_async.side_effect = ConnectionError("broker down")
        resp = client.post(f"/sources/{source.source_id}/sync", headers=_auth())

    assert resp.status_code == 503
    tracker.release_lock.assert_awaited_once_with(source.source_id, source.tenant_id, "job-9")


async def test_the_task_adopts_the_lock_the_api_took_and_uses_its_job_id() -> None:
    from app.ingestion.scheduler import _sync_source_async

    source = _make_source()
    tracker = install_lease(AsyncMock())
    tracker.create_job = AsyncMock(
        return_value=IngestionJob(
            job_id="job-123",
            source_id=source.source_id,
            tenant_id=source.tenant_id,
            status="running",
            sync_mode="incremental",
        )
    )
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=source)
    pipeline = AsyncMock()
    pipeline.ingest = AsyncMock(
        return_value=PipelineResult(doc_id="d", source_id="s", tenant_id="t", status="indexed")
    )

    class _Connector:
        async def get_delta(self, config, cursor):  # type: ignore[no-untyped-def]
            if False:  # pragma: no cover
                yield

    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, source_store),
        ),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
    ):
        result = await _sync_source_async(
            task=MagicMock(),
            source_id=source.source_id,
            tenant_id=source.tenant_id,
            triggered_by="manual",
            job_id="job-123",
        )

    tracker.acquire_lock.assert_not_awaited()  # the API already holds it
    assert tracker.create_job.await_args.kwargs["job_id"] == "job-123"
    assert result["job_id"] == "job-123"
    # The worker holds the API's lock under its token (TG-12) and releases it.
    tracker.hold.assert_awaited_once()
    assert tracker.hold.await_args.args[2] == "job-123"
    tracker.release_lock.assert_awaited_once_with(source.source_id, source.tenant_id, "job-123")
