"""KB-10: repository ingestion is a durable, capped Celery job whose DLQ is replayed.

``POST /knowledge/ingest/repo`` spawned ``asyncio.create_task`` on the API
replica — no per-tenant cap (a burst of 100 MiB clones could exhaust the pod),
lost on restart — and repository DLQ rows were marked permanent_failure because
nothing could replay them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.rag.models import KnowledgeCollection
from tests.api.test_knowledge_persistence import API_KEY, TENANT, _app, _AwaitedStore

_BODY = {"collection_id": "collection-1", "repo_url": "https://github.com/example/repository"}


def _store(active: int = 0) -> _AwaitedStore:
    store = _AwaitedStore()
    store.seed_collection(KnowledgeCollection(name="repository", collection_id="collection-1"))
    store.count_active_ingestion_jobs_async = AsyncMock(return_value=active)  # type: ignore[method-assign]
    return store


def _post(store: _AwaitedStore) -> Any:
    source = SimpleNamespace(
        url="https://github.com/example/repository", curl_resolve="github.com:443:1.2.3.4"
    )
    with patch("app.api.knowledge.resolve_repository_source", return_value=source):
        return TestClient(_app(store), raise_server_exceptions=False).post(
            "/knowledge/ingest/repo", json=_BODY, headers={"X-API-Key": API_KEY}
        )


def test_repo_ingest_enqueues_the_durable_task_not_an_in_process_task() -> None:
    store = _store()
    with (
        patch("app.ingestion.repo_tasks.ingest_repository_task") as task,
        patch("asyncio.create_task") as create_task,
    ):
        resp = _post(store)

    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"] == "job-1"
    create_task.assert_not_called()
    task.apply_async.assert_called_once()
    call = task.apply_async.call_args.kwargs
    assert call["queue"] == "ingestion"
    params = call["kwargs"]
    assert params["job_id"] == "job-1"
    assert params["tenant_id"] == TENANT.tenant_id
    assert params["repo_url"] == "https://github.com/example/repository"
    assert params["collection_id"] == "collection-1"


def test_repo_ingest_past_the_tenant_cap_is_429() -> None:
    from app.core.config import get_settings

    store = _store(active=get_settings().repo_ingest_max_concurrent_per_tenant)
    with patch("app.ingestion.repo_tasks.ingest_repository_task") as task:
        resp = _post(store)

    assert resp.status_code == 429, resp.text
    assert store.jobs == {}
    task.apply_async.assert_not_called()


def test_a_failed_enqueue_fails_the_job_and_is_503() -> None:
    store = _store()
    with patch("app.ingestion.repo_tasks.ingest_repository_task") as task:
        task.apply_async.side_effect = ConnectionError("broker down")
        resp = _post(store)

    assert resp.status_code == 503
    assert store.jobs["job-1"]["status"] == "failed"


async def test_the_worker_task_runs_the_ingestion_with_worker_services() -> None:
    from app.ingestion import repo_tasks

    worker_store, embedder, tracker = MagicMock(), MagicMock(), MagicMock()
    with (
        patch.object(
            repo_tasks, "_worker_services", return_value=(worker_store, embedder, tracker)
        ),
        patch("app.api.knowledge._ingest_repo_background", new=AsyncMock()) as run,
    ):
        await repo_tasks._run_repo_ingest_async(
            job_id="job-1",
            tenant_id="t-1",
            repo_url="https://github.com/example/repository",
            collection_id="c",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            dlq_attempt=2,
        )

    kwargs = run.await_args.kwargs
    assert kwargs["store"] is worker_store and kwargs["embedder"] is embedder
    assert kwargs["job_tracker"] is tracker
    assert kwargs["tenant_ctx"].tenant_id == "t-1"
    assert kwargs["dlq_attempt"] == 2


def _entry(attempt: int) -> dict[str, Any]:
    return {
        "dlq_id": "dlq-1",
        "tenant_id": "t-1",
        "source_id": "repo:https://github.com/example/repository",
        "doc_id": "job-old",
        "retry_count": 0,
        "raw_doc_json": json.dumps(
            {
                "kind": "repository",
                "job_id": "job-old",
                "repo_url": "https://github.com/example/repository",
                "collection_id": "c",
                "branch": "main",
                "file_patterns": ["**/*.py"],
                "max_files": 10,
                "dlq_attempt": attempt,
            }
        ),
    }


async def test_a_repository_dlq_row_is_replayed_as_a_new_job() -> None:
    from app.ingestion import scheduler

    tracker = AsyncMock()
    tracker.get_retryable_dlq_entries = AsyncMock(return_value=[_entry(0)])
    worker_store = MagicMock()
    worker_store.create_ingestion_job_async = AsyncMock(return_value="job-new")
    with (
        patch.object(
            scheduler, "_build_worker_ingestion", return_value=(tracker, AsyncMock(), AsyncMock())
        ),
        patch("app.ingestion.repo_tasks._worker_services", return_value=(worker_store, None, None)),
        patch("app.ingestion.repo_tasks.ingest_repository_task") as task,
    ):
        result = await scheduler._retry_dlq_async()

    params = task.apply_async.call_args.kwargs["kwargs"]
    assert params["job_id"] == "job-new"
    assert params["dlq_attempt"] == 1
    tracker.resolve_dlq_entry.assert_awaited_once_with("dlq-1", "t-1")
    tracker.mark_dlq_permanent_failure.assert_not_awaited()
    assert result["retried"] == 1


async def test_a_repository_dlq_row_past_the_retry_limit_is_permanent() -> None:
    from app.ingestion import scheduler

    tracker = AsyncMock()
    tracker.get_retryable_dlq_entries = AsyncMock(
        return_value=[_entry(scheduler._DLQ_MAX_RETRIES)]
    )
    with (
        patch.object(
            scheduler, "_build_worker_ingestion", return_value=(tracker, AsyncMock(), AsyncMock())
        ),
        patch("app.ingestion.repo_tasks.ingest_repository_task") as task,
    ):
        await scheduler._retry_dlq_async()

    task.apply_async.assert_not_called()
    tracker.mark_dlq_permanent_failure.assert_awaited_once_with("dlq-1", "t-1")
