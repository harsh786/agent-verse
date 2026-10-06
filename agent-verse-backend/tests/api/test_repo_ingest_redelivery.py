"""a04-F066-03: a worker dying mid-clone; the acks_late redelivery takes the job over.

The repository task is ``acks_late`` (with ``task_reject_on_worker_lost``), so
a worker killed mid-clone has its message redelivered. The redelivery used to
find the job ``running`` under the dead worker's lease, fail its claim, and
dead-letter a duplicate while the job sat ``running`` until reconciliation
failed it as interrupted — nobody ever finished it. Now:

* a live lease held by another worker → the delivery neither fails nor
  dead-letters the job; the task retries after one lease period;
* an expired lease (the dead worker's) → the redelivery claims and finishes it
  (the takeover itself is SQL: tests/rag/test_persisted_rag_store.py);
* a job already completed / failed → a duplicate delivery is a no-op, except
  one reconciliation failed as *interrupted* while the dead worker's task
  waited for redelivery: that worker never dead-lettered it, so the
  redelivery does (the DLQ retry re-runs it; terminal jobs are immutable).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.knowledge import RepositoryJobLeaseHeldError, _ingest_repo_background
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-redeliver", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_REPO = "https://github.com/example/repository"


def _kwargs(store: Any, tracker: Any) -> dict[str, Any]:
    return {
        "job_id": "job-r",
        "repo_url": _REPO,
        "collection_id": "collection-1",
        "branch": "main",
        "file_patterns": ["**/*.py"],
        "max_files": 10,
        "store": store,
        "embedder": None,
        "tenant_ctx": _CTX,
        "curl_resolve": "github.com:443:203.0.113.10",
        "job_tracker": tracker,
        "lease_seconds": 60,
    }


def _store(status: str | None, error: str | None = None) -> MagicMock:
    store = MagicMock()
    store.claim_ingestion_job_async = AsyncMock(return_value=False)
    store.get_ingestion_job_async = AsyncMock(
        return_value=None
        if status is None
        else {"job_id": "job-r", "status": status, "error_message": error}
    )
    store.fail_ingestion_job_async = AsyncMock()
    return store


def _tracker() -> MagicMock:
    tracker = MagicMock()
    tracker.dead_letter_sourceless = AsyncMock(return_value="dlq-1")
    return tracker


async def test_a_live_lease_elsewhere_is_neither_failed_nor_dead_lettered() -> None:
    store, tracker = _store("running"), _tracker()
    with (
        patch("asyncio.create_subprocess_exec") as git,
        pytest.raises(RepositoryJobLeaseHeldError) as raised,
    ):
        await _ingest_repo_background(**_kwargs(store, tracker))
    assert raised.value.retry_after_seconds == 61  # one lease period
    git.assert_not_called()
    store.fail_ingestion_job_async.assert_not_awaited()
    tracker.dead_letter_sourceless.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "error"),
    [("completed", None), ("failed", "Repository ingestion failed")],
)
async def test_a_duplicate_delivery_of_a_finished_job_is_a_no_op(
    status: str, error: str | None
) -> None:
    store, tracker = _store(status, error), _tracker()
    with patch("asyncio.create_subprocess_exec") as git:
        await _ingest_repo_background(**_kwargs(store, tracker))
    git.assert_not_called()
    store.fail_ingestion_job_async.assert_not_awaited()
    tracker.dead_letter_sourceless.assert_not_awaited()


async def test_redelivery_of_a_job_reconciled_as_interrupted_dead_letters_it() -> None:
    store, tracker = _store("failed", "Repository ingestion interrupted"), _tracker()
    with patch("asyncio.create_subprocess_exec") as git:
        await _ingest_repo_background(**_kwargs(store, tracker))
    git.assert_not_called()
    store.fail_ingestion_job_async.assert_not_awaited()  # already terminal
    tracker.dead_letter_sourceless.assert_awaited_once()
    payload = tracker.dead_letter_sourceless.await_args.kwargs["payload"]
    assert payload["kind"] == "repository" and payload["repo_url"] == _REPO


async def test_redelivery_after_the_lease_expired_finishes_the_job(tmp_path: Path) -> None:
    """The dead worker's job: the redelivery claims it (takeover) and completes it."""
    from app.providers.fake import FakeProvider

    (tmp_path / "service.py").write_text("def service():\n    return 'ok'\n")
    store = MagicMock()
    claims: list[str] = []

    async def _claim(job_id: str, **kwargs: Any) -> bool:
        claims.append(kwargs["lease_owner"])
        return True  # the SQL takeover of an expired lease (integration-tested)

    store.claim_ingestion_job_async = AsyncMock(side_effect=_claim)
    store.heartbeat_ingestion_job_async = AsyncMock(return_value=True)
    store.ingest_repository_chunks_async = AsyncMock()
    store.fail_ingestion_job_async = AsyncMock()
    process = AsyncMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"", b""))
    tracker = _tracker()
    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("shutil.rmtree"),
    ):
        await _ingest_repo_background(
            **{**_kwargs(store, tracker), "embedder": FakeProvider(embed_dim=768)}
        )
    assert len(claims) == 1
    completion = store.ingest_repository_chunks_async.await_args.kwargs
    assert completion["lease_owner"] == claims[0]  # fenced by the new owner
    store.fail_ingestion_job_async.assert_not_awaited()
    tracker.dead_letter_sourceless.assert_not_awaited()


# ── the Celery task ──────────────────────────────────────────────────────────


class _RetryError(Exception):
    pass


def _bound_task(retries: int) -> MagicMock:
    task = MagicMock()
    task.request.retries = retries
    task.retry.side_effect = lambda **kw: _RetryError(kw)
    return task


def test_task_retries_after_one_lease_period_when_the_lease_is_held() -> None:
    from app.ingestion import repo_tasks

    task = _bound_task(retries=0)
    with (
        patch.object(repo_tasks, "_run_repo_ingest_async", MagicMock()),
        patch.object(
            repo_tasks,
            "_run_task_loop",
            side_effect=RepositoryJobLeaseHeldError("job-r", retry_after_seconds=61),
        ),
        pytest.raises(_RetryError),
    ):
        repo_tasks.ingest_repository_task.run.__func__(task, job_id="job-r")  # type: ignore[attr-defined]
    kwargs = task.retry.call_args.kwargs
    assert kwargs["countdown"] == 61
    assert kwargs["max_retries"] == repo_tasks.LEASE_HELD_MAX_RETRIES


def test_task_drops_the_duplicate_once_the_holder_proved_alive() -> None:
    from app.ingestion import repo_tasks

    task = _bound_task(retries=repo_tasks.LEASE_HELD_MAX_RETRIES)
    with (
        patch.object(repo_tasks, "_run_repo_ingest_async", MagicMock()),
        patch.object(
            repo_tasks,
            "_run_task_loop",
            side_effect=RepositoryJobLeaseHeldError("job-r", retry_after_seconds=61),
        ),
    ):
        out = repo_tasks.ingest_repository_task.run.__func__(task, job_id="job-r")  # type: ignore[attr-defined]
    assert out == {"job_id": "job-r", "status": "lease_held"}
    task.retry.assert_not_called()


def test_the_task_is_redelivered_when_its_worker_dies() -> None:
    from app.ingestion.repo_tasks import ingest_repository_task
    from app.scaling.celery_app import celery_app

    assert ingest_repository_task.acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
