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

from app.db.rls import sqlalchemy_rls_context, system_session
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
        self._locks: dict[str, str] = {}  # source_id → job_id

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
                        source_id,
                        existing,
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

    async def release_lock(self, source_id: str, tenant_id: str, job_id: str | None = None) -> None:
        """Release the distributed ingestion lock."""
        lock_key = f"ingestion_lock:{tenant_id}:{source_id}"
        if self._redis is not None:
            try:
                if job_id:
                    stored = await self._redis.get(lock_key)
                    if stored and stored.decode() == job_id:
                        await self._redis.delete(lock_key)
                else:
                    await self._redis.delete(lock_key)
                return
            except Exception as e:
                _log.debug("ingestion_lock_release_error: %s", e)
        # In-memory fallback
        if job_id:
            if self._locks.get(source_id) == job_id:
                del self._locks[source_id]
        else:
            self._locks.pop(source_id, None)

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
        job.docs_discovered += indexed + skipped + failed

    async def complete_job(self, job: IngestionJob, *, error: str = "") -> None:
        """Mark the job as completed or failed."""
        job.status = "failed" if error else "completed"
        job.error_message = error[:2048] if error else ""
        job.completed_at = datetime.now(UTC).isoformat()

        _log.info(
            "ingestion_job_complete job=%s status=%s indexed=%d skipped=%d failed=%d chunks=%d",
            job.job_id,
            job.status,
            job.docs_indexed,
            job.docs_skipped,
            job.docs_failed,
            job.chunks_created,
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

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
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

    # ── Methods required by scheduler.py ────────────────────────────────────

    async def load_config(self, source_id: str, tenant_id: str) -> SourceConfig | None:
        """Load a SourceConfig from DB or in-memory store. Returns None if not found."""
        from app.ingestion.source_config import SourceConfig, SourceFamily

        # Try in-memory first (populated during sync loop)
        if source_id in self._jobs:
            config = SourceConfig(
                source_id=source_id,
                tenant_id=tenant_id,
                name=source_id,
                family=SourceFamily.AGENT_GENERATED,
                source_type="unknown",
                enabled=True,
                sync_mode="incremental",
                connection_config={},
            )
            config.cursor_value = self._source_cursors.get(source_id, "")
            return config
        try:
            from sqlalchemy import text

            # load_config is scoped to one tenant — per-tenant RLS, not the
            # cross-tenant system_session that get_due_sources needs.
            async with (
                self._db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = await session.execute(
                    text("SELECT * FROM source_configs WHERE source_id = :id AND tenant_id = :tid"),
                    {"id": source_id, "tid": tenant_id},
                )
                data = row.mappings().first()
                if data:
                    return SourceConfig(**dict(data))
        except Exception as exc:
            _log.debug("load_config_error: %s", exc)
        return None

    async def get_due_sources(self) -> list[tuple[str, str]]:
        """Return list of (source_id, tenant_id) tuples whose next sync is due."""
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                system_session(session),
            ):
                result = await session.execute(
                    text("""
                        SELECT source_id, tenant_id
                          FROM source_configs
                         WHERE enabled = true
                           AND sync_mode != 'streaming'
                           AND (
                               last_synced_at IS NULL
                               OR last_synced_at + (sync_interval_seconds || ' seconds')::interval
                                  <= NOW()
                           )
                    """)
                )
                return [(row.source_id, row.tenant_id) for row in result]
        except Exception as exc:
            _log.debug("get_due_sources_error: %s", exc)
            return []

    async def increment_failure_counter(self, source_id: str, tenant_id: str) -> None:
        """Increment consecutive failure counter on a source."""
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE source_configs SET consecutive_failures = COALESCE(consecutive_failures, 0) + 1 WHERE source_id = :id AND tenant_id = :tid"  # noqa: E501
                    ),
                    {"id": source_id, "tid": tenant_id},
                )
        except Exception as exc:
            _log.debug("increment_failure_counter_error: %s", exc)

    async def reset_failure_counter(self, source_id: str, tenant_id: str) -> None:
        """Reset consecutive failure counter after a successful sync."""
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE source_configs SET consecutive_failures = 0 WHERE source_id = :id AND tenant_id = :tid"  # noqa: E501
                    ),
                    {"id": source_id, "tid": tenant_id},
                )
        except Exception as exc:
            _log.debug("reset_failure_counter_error: %s", exc)

    async def add_to_dlq(
        self,
        *,
        source_id: str,
        tenant_id: str,
        doc_id: str,
        error: str,
        raw_doc: object,
    ) -> None:
        """Add a failed document to the DLQ.

        ``raw_doc`` is serialized into ``raw_doc_json`` so the entry carries the
        payload needed to retry (e.g. the repo-ingest parameters). It may be a
        dataclass, a pydantic model, a mapping, or anything JSON-serializable;
        non-serializable values fall back to a minimal ``{"doc_id": ...}`` record.
        """
        import dataclasses as _dc
        import json as _json
        import uuid as _uuid

        def _serialize(obj: object) -> str:
            payload: object = {"doc_id": doc_id}
            if obj is not None:
                if _dc.is_dataclass(obj) and not isinstance(obj, type):
                    payload = _dc.asdict(obj)
                elif hasattr(obj, "model_dump"):
                    payload = obj.model_dump()  # type: ignore[attr-defined]
                elif isinstance(obj, dict):
                    payload = obj
            try:
                return _json.dumps(payload, default=str)
            except (TypeError, ValueError):
                return _json.dumps({"doc_id": doc_id})

        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO ingestion_dlq
                            (dlq_id, source_id, tenant_id, doc_id, error_message,
                             raw_doc_json, retry_count, created_at)
                        VALUES
                            (:dlq_id, :source_id, :tenant_id, :doc_id, :error,
                             :raw_doc_json, 0, NOW())
                    """),
                    {
                        "dlq_id": str(_uuid.uuid4()),
                        "source_id": source_id,
                        "tenant_id": tenant_id,
                        "doc_id": doc_id,
                        "error": error,
                        "raw_doc_json": _serialize(raw_doc),
                    },
                )
        except Exception as exc:
            _log.debug("add_to_dlq_error: %s", exc)

    async def get_retryable_dlq_entries(self, max_entries: int = 50) -> list[object]:
        """Return DLQ entries eligible for retry (retry_count < 5, not permanent)."""
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text("""
                        SELECT * FROM ingestion_dlq
                         WHERE retry_count < 5
                           AND permanent_failure IS NOT TRUE
                         ORDER BY created_at ASC
                         LIMIT :limit
                    """),
                    {"limit": max_entries},
                )
                return list(result.mappings())
        except Exception as exc:
            _log.debug("get_retryable_dlq_entries_error: %s", exc)
            return []

    async def resolve_dlq_entry(self, dlq_id: str) -> None:
        """Mark a DLQ entry as resolved (successfully retried)."""
        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text("DELETE FROM ingestion_dlq WHERE dlq_id = :id"),
                    {"id": dlq_id},
                )
        except Exception as exc:
            _log.debug("resolve_dlq_entry_error: %s", exc)

    async def increment_dlq_retry(self, dlq_id: str, error: str = "") -> None:
        """Increment retry count on a DLQ entry."""
        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE ingestion_dlq SET retry_count = retry_count + 1, last_error = :error, last_retried_at = NOW() WHERE dlq_id = :id"  # noqa: E501
                    ),
                    {"id": dlq_id, "error": error},
                )
        except Exception as exc:
            _log.debug("increment_dlq_retry_error: %s", exc)

    async def mark_dlq_permanent_failure(self, dlq_id: str) -> None:
        """Mark a DLQ entry as permanently failed (no more retries)."""
        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text("UPDATE ingestion_dlq SET permanent_failure = true WHERE dlq_id = :id"),
                    {"id": dlq_id},
                )
        except Exception as exc:
            _log.debug("mark_dlq_permanent_failure_error: %s", exc)
