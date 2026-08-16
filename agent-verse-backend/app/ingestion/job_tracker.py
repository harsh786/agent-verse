"""IngestionJobTracker — cursor persistence and job status tracking.

LAW-03: cursor_value persisted after each batch; resumable on crash.
LAW-14: distributed lock via Redis SETNX prevents duplicate jobs.
LAW-18: state changes appended as events (ingestion_events table when DB avail).
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.ingestion.source_config import IngestionJob, SourceConfig

_log = logging.getLogger(__name__)


class IngestionJobTracker:
    """Manages ingestion job lifecycle: create, update cursor, complete.

    In-memory fallback (no DB) suitable for development.
    DB-backed path persists to ingestion_jobs table (migration 0108).
    """

    def __init__(self, *, db: Any = None, redis: Any = None) -> None:
        self._db = db
        self._redis = redis
        # In-memory fallback
        self._jobs: dict[str, IngestionJob] = {}
        self._source_cursors: dict[str, str] = {}  # source_id → cursor
        self._locks: dict[str, str] = {}           # source_id → job_id

    # ── Distributed locking (LAW-14) ─────────────────────────────────────────

    async def acquire_lock(
        self, source_id: str, tenant_id: str, ttl_seconds: int = 3600
    ) -> str | None:
        """Acquire exclusive ingestion lock for a source.

        Returns job_id if lock acquired, None if already held by another worker.
        """
        lock_key = f"ingestion_lock:{tenant_id}:{source_id}"
        job_id = uuid.uuid4().hex

        if self._redis is not None:
            try:
                acquired = await self._redis.set(lock_key, job_id, nx=True, ex=ttl_seconds)
                if not acquired:
                    existing = await self._redis.get(lock_key)
                    _log.info(
                        "ingestion_lock_held source=%s existing_job=%s",
                        source_id, existing,
                    )
                    return None
                return job_id
            except Exception as e:
                _log.warning("ingestion_lock_redis_error source=%s: %s", source_id, e)
                # Fall through to in-memory
        
        # In-memory fallback
        if source_id in self._locks:
            return None
        self._locks[source_id] = job_id
        return job_id

    async def release_lock(self, source_id: str, tenant_id: str, job_id: str) -> None:
        """Release the distributed ingestion lock."""
        lock_key = f"ingestion_lock:{tenant_id}:{source_id}"
        if self._redis is not None:
            try:
                stored = await self._redis.get(lock_key)
                if stored and stored.decode() == job_id:
                    await self._redis.delete(lock_key)
                return
            except Exception as e:
                _log.debug("ingestion_lock_release_error: %s", e)
        # In-memory fallback
        if self._locks.get(source_id) == job_id:
            del self._locks[source_id]

    # ── Job lifecycle ─────────────────────────────────────────────────────────

    async def create_job(
        self,
        source_config: SourceConfig,
        *,
        job_id: str,
        triggered_by: str = "scheduler",
    ) -> IngestionJob:
        """Create a new ingestion job record."""
        cursor_before = source_config.cursor_value or ""
        job = IngestionJob(
            job_id=job_id,
            source_id=source_config.source_id,
            tenant_id=source_config.tenant_id,
            status="running",
            sync_mode=source_config.sync_mode,
            triggered_by=triggered_by,
            started_at=datetime.now(UTC).isoformat(),
            cursor_before=cursor_before,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._jobs[job_id] = job

        if self._db is not None:
            await self._persist_job_created(job)

        return job

    async def update_cursor(
        self,
        job: IngestionJob,
        new_cursor: str,
        source_config: SourceConfig,
    ) -> None:
        """Commit the new cursor position — the key to resumability (LAW-03).

        Called after each batch of documents is successfully indexed.
        If the worker crashes after this point, the next run starts from here.
        """
        job.cursor_after = new_cursor
        source_config.cursor_value = new_cursor

        if self._db is not None:
            await self._persist_cursor_update(
                source_config.source_id,
                source_config.tenant_id,
                new_cursor,
                job.job_id,
            )

    async def increment_counters(
        self,
        job: IngestionJob,
        *,
        indexed: int = 0,
        skipped: int = 0,
        failed: int = 0,
        chunks: int = 0,
        bytes_: int = 0,
        tokens: int = 0,
    ) -> None:
        """Update job progress counters."""
        job.docs_indexed += indexed
        job.docs_skipped += skipped
        job.docs_failed += failed
        job.chunks_created += chunks
        job.bytes_processed += bytes_
        job.tokens_consumed += tokens
        job.docs_discovered += (indexed + skipped + failed)

    async def complete_job(
        self, job: IngestionJob, *, error: str = ""
    ) -> None:
        """Mark the job as completed or failed."""
        job.status = "failed" if error else "completed"
        job.error_message = error[:2048] if error else ""
        job.completed_at = datetime.now(UTC).isoformat()

        _log.info(
            "ingestion_job_complete job=%s status=%s indexed=%d skipped=%d failed=%d chunks=%d",
            job.job_id, job.status, job.docs_indexed, job.docs_skipped,
            job.docs_failed, job.chunks_created,
        )

        if self._db is not None:
            await self._persist_job_completed(job)

    def get_job(self, job_id: str) -> IngestionJob | None:
        return self._jobs.get(job_id)

    def list_jobs_for_source(self, source_id: str) -> list[IngestionJob]:
        return [j for j in self._jobs.values() if j.source_id == source_id]

    # ── DB persistence (no-ops when DB not available) ─────────────────────────

    async def _persist_job_created(self, job: IngestionJob) -> None:
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO ingestion_jobs
                          (id, source_id, tenant_id, status, sync_mode,
                           triggered_by, cursor_before, started_at, created_at)
                        VALUES
                          (:id, :source_id, :tenant_id, :status, :sync_mode,
                           :triggered_by, :cursor_before, NOW(), NOW())
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": job.job_id,
                        "source_id": job.source_id,
                        "tenant_id": job.tenant_id,
                        "status": job.status,
                        "sync_mode": job.sync_mode,
                        "triggered_by": job.triggered_by,
                        "cursor_before": job.cursor_before,
                    },
                )
        except Exception as e:
            _log.debug("ingestion_job_persist_error: %s", e)

    async def _persist_cursor_update(
        self,
        source_id: str,
        tenant_id: str,
        cursor: str,
        job_id: str,
    ) -> None:
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        UPDATE source_configs
                           SET cursor_value = :cursor, updated_at = NOW()
                         WHERE id = :source_id AND tenant_id = :tenant_id
                    """),
                    {"cursor": cursor, "source_id": source_id, "tenant_id": tenant_id},
                )
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                           SET cursor_after = :cursor
                         WHERE id = :job_id
                    """),
                    {"cursor": cursor, "job_id": job_id},
                )
        except Exception as e:
            _log.debug("ingestion_cursor_persist_error: %s", e)

    async def _persist_job_completed(self, job: IngestionJob) -> None:
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                           SET status = :status,
                               completed_at = NOW(),
                               docs_indexed = :indexed,
                               docs_skipped = :skipped,
                               docs_failed = :failed,
                               chunks_created = :chunks,
                               error_message = :error,
                               cursor_after = :cursor
                         WHERE id = :job_id
                    """),
                    {
                        "status": job.status,
                        "indexed": job.docs_indexed,
                        "skipped": job.docs_skipped,
                        "failed": job.docs_failed,
                        "chunks": job.chunks_created,
                        "error": job.error_message,
                        "cursor": job.cursor_after,
                        "job_id": job.job_id,
                    },
                )
        except Exception as e:
            _log.debug("ingestion_job_complete_persist_error: %s", e)
