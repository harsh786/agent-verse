"""Durable repository (git clone) ingestion on the Celery ingestion queue.

``POST /knowledge/ingest/repo`` used to run the clone + index as an
``asyncio.create_task`` inside the API replica: a restart lost it, nothing capped
how many 100 MiB clones one tenant could start on a pod, and repository DLQ rows
could never be replayed. The route now creates the durable job row and enqueues
:func:`ingest_repository_task`; the DLQ retry job re-enqueues failed repository
jobs (``kind: "repository"`` rows) through :func:`replay_repository_dlq_entry`.

The ingestion body itself (clone under disk/file quotas, secret scan, PII +
RAG_INGEST screening, lease heartbeats, atomic index, dead-lettering) is
unchanged: ``app.api.knowledge._ingest_repo_background``.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task  # type: ignore[import-not-found]

from app.observability.logging import get_logger

_log = get_logger(__name__)


def _run_task_loop(coro: Any) -> Any:
    """Celery entry → async body on a fresh, fully torn-down loop.

    The persistent ``get_event_loop()`` shared the module-level DB engine with
    the scaling tasks' throw-away loops in the same worker process, so pooled
    asyncpg connections crossed loops and leaked "idle in transaction".
    """
    from app.db.session import run_in_fresh_loop

    return run_in_fresh_loop(coro)


def _worker_services() -> tuple[Any, Any, Any]:
    """(DB-backed KnowledgeStore, query embedder, DB-backed job tracker) for a worker.

    Built by the shared worker builder, which also binds the guardrail engine to
    the tenant's persisted rules (RV-06): without it, a fresh worker refused to
    screen repository files in production and ignored tenant block rules
    elsewhere.
    """
    from app.db.session import get_session_factory, get_system_session_factory
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.worker_services import build_worker_knowledge_services

    db_factory = get_session_factory()
    store, embedder = build_worker_knowledge_services(db_factory)
    tracker = IngestionJobTracker(db=db_factory, system_db=get_system_session_factory())
    return store, embedder, tracker


def _tenant_context(tenant_id: str) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="repo-ingest")


async def _run_repo_ingest_async(
    *,
    job_id: str,
    tenant_id: str,
    repo_url: str,
    collection_id: str,
    branch: str,
    file_patterns: list[str],
    max_files: int,
    dlq_attempt: int = 0,
) -> None:
    from app.api.knowledge import _ingest_repo_background
    from app.core.config import get_settings
    from app.ingestion.repository_security import RepositoryLimits

    settings = get_settings()
    store, embedder, tracker = _worker_services()
    capped_files = min(max_files, settings.repo_ingest_max_files)
    await _ingest_repo_background(
        job_id=job_id,
        repo_url=repo_url,
        collection_id=collection_id,
        branch=branch,
        file_patterns=file_patterns,
        max_files=capped_files,
        store=store,
        embedder=embedder,
        tenant_ctx=_tenant_context(tenant_id),
        limits=RepositoryLimits(
            max_files=capped_files,
            max_file_bytes=settings.repo_ingest_max_file_bytes,
            max_total_bytes=settings.repo_ingest_max_total_bytes,
            max_repository_bytes=settings.repo_ingest_max_repository_bytes,
            max_repository_files=settings.repo_ingest_max_repository_files,
        ),
        clone_timeout_seconds=settings.repo_ingest_clone_timeout_seconds,
        # Resolved (and SSRF-checked + pinned for git) here, next to the clone.
        curl_resolve=None,
        lease_seconds=settings.repo_ingest_lease_seconds,
        heartbeat_seconds=settings.repo_ingest_heartbeat_seconds,
        job_tracker=tracker,
        dlq_attempt=dlq_attempt,
    )


@shared_task(name="ingestion.ingest_repository", bind=True, acks_late=True)
def ingest_repository_task(self: Any, **params: Any) -> dict[str, Any]:
    """Celery entry point; failures are recorded on the job row and dead-lettered."""
    _run_task_loop(_run_repo_ingest_async(**params))
    return {"job_id": params.get("job_id")}


def enqueue_repository_ingest(**params: Any) -> None:
    """Queue one repository ingestion on the ingestion queue (raises if it can't)."""
    ingest_repository_task.apply_async(kwargs=params, queue="ingestion")


async def replay_repository_dlq_entry(
    payload: dict[str, Any], *, tenant_id: str, max_attempts: int
) -> bool:
    """Re-enqueue a dead-lettered repository ingestion as a new job.

    Returns False (the caller marks the row permanent) once the payload has
    been replayed ``max_attempts`` times — each replay that fails again writes
    a fresh DLQ row carrying the incremented attempt count.
    """
    attempt = int(payload.get("dlq_attempt") or 0)
    if attempt >= max_attempts:
        return False
    repo_url = str(payload.get("repo_url") or "")
    collection_id = str(payload.get("collection_id") or "")
    if not repo_url or not collection_id:
        return False
    store, _embedder, _tracker = _worker_services()
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=repo_url,
        source_type="repository",
        title=repo_url.rstrip("/").rsplit("/", 2)[-1],
        tenant_ctx=_tenant_context(tenant_id),
    )
    enqueue_repository_ingest(
        job_id=job_id,
        tenant_id=tenant_id,
        repo_url=repo_url,
        collection_id=collection_id,
        branch=str(payload.get("branch") or "main"),
        file_patterns=list(payload.get("file_patterns") or []),
        max_files=int(payload.get("max_files") or 1),
        dlq_attempt=attempt + 1,
    )
    _log.info("repo_ingest_dlq_replayed", job_id=job_id, attempt=attempt + 1)
    return True
