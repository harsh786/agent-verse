"""IngestionJobTracker — cursor persistence and job status tracking.

LAW-03: cursor_value persisted after each batch; resumable on crash.
LAW-14: distributed lock via Redis SETNX prevents duplicate jobs.
LAW-18: state changes appended as events (ingestion_events table when DB avail).

Database posture (the API runs as a NOBYPASSRLS role):

* ``db`` is the application's own session factory. Everything that acts on one
  tenant's rows — job records, cursors, failure counters, DLQ inserts and the
  per-entry DLQ follow-ups — runs there under ``sqlalchemy_rls_context`` with an
  explicit ``tenant_id`` predicate as well.
* ``system_db`` is the maintenance-role (BYPASSRLS) factory, used only by the two
  genuinely cross-tenant Celery-beat scans: ``get_due_sources`` and
  ``get_retryable_dlq_entries``. It defaults to ``get_system_session_factory()``.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context, system_session
from app.ingestion.source_config import IngestionJob, RawDocument, SourceConfig

# Marks base64-encoded bytes inside a DLQ ``raw_doc_json`` payload.
_BYTES_MARKER = "__bytes_b64__"

_log = logging.getLogger(__name__)


def _orphan_reason(older_than_seconds: int) -> str:
    return (
        f"orphaned: no worker finished this job within {older_than_seconds}s "
        "(the worker was lost or restarted); trigger the sync again"
    )


def _as_text(value: object) -> str:
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def attach_shared_redis(tracker: object, redis: object) -> bool:
    """Give a tracker that has no Redis the shared client (P1b-1).

    The sync lock and the cancel flag must be visible to every process: the API
    takes the lock and the Celery worker releases it, the API sets the cancel
    flag and the worker reads it. With a per-process (in-memory) tracker the
    worker could never release the API's lock — every later manual sync was
    ``already_running`` — and a scheduled sync ran beside a manual one. Returns
    True when the client was attached.
    """
    if tracker is None or redis is None or not hasattr(tracker, "_redis"):
        return False
    if getattr(tracker, "_redis", None) is not None:
        return False
    setattr(tracker, "_redis", redis)  # noqa: B010 - private slot of the tracker
    return True


class IngestionJobTracker:
    """Manages ingestion job lifecycle: create, update cursor, complete.

    In-memory fallback (no DB) suitable for development.
    DB-backed path persists to ingestion_jobs table (migration 0108).
    """

    def __init__(self, *, db: Any = None, redis: Any = None, system_db: Any = None) -> None:
        self._db = db
        self._system_db = system_db
        self._redis = redis
        # In-memory fallback
        self._jobs: dict[str, IngestionJob] = {}
        self._source_cursors: dict[str, str] = {}  # source_id → cursor
        self._locks: dict[str, str] = {}  # source_id → job_id
        self._cancelled: set[str] = set()  # job ids with a cancel request

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
                    # The API's pooled client decodes responses (str); others
                    # return bytes. ``stored.decode()`` on a str raised here and
                    # the lock was never released (P1b-1).
                    if stored is not None and _as_text(stored) == job_id:
                        await self._redis.delete(lock_key)
                else:
                    await self._redis.delete(lock_key)
                return
            except Exception as e:
                _log.warning("ingestion_lock_release_error source=%s: %s", source_id, e)
        # In-memory fallback
        if job_id:
            if self._locks.get(source_id) == job_id:
                del self._locks[source_id]
        else:
            self._locks.pop(source_id, None)

    async def running_job_id(self, source_id: str, tenant_id: str) -> str | None:
        """The job id holding the source's sync lock (a sync is running), or None."""
        if self._redis is not None:
            value = await self._redis.get(f"ingestion_lock:{tenant_id}:{source_id}")
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes | bytearray) else str(value)
        return self._locks.get(source_id)

    # ── Cancellation (KB-15) ──────────────────────────────────────────────────
    # A cancel request is a Redis flag keyed by job id, so the API replica that
    # receives it and the worker running the sync need not be the same process.
    # The sync loop checks it between documents and stops cleanly: what was
    # indexed stays indexed and the cursor is committed, so the next sync resumes.

    async def request_cancel(self, source_id: str, tenant_id: str) -> str | None:
        """Flag the running sync of a source for cancellation; its job id, or None."""
        job_id = await self.running_job_id(source_id, tenant_id)
        if job_id is None:
            return None
        if self._redis is not None:
            await self._redis.set(f"ingestion_cancel:{tenant_id}:{job_id}", "1", ex=3600)
        else:
            self._cancelled.add(job_id)
        return job_id

    async def is_cancel_requested(self, tenant_id: str, job_id: str) -> bool:
        if self._redis is not None:
            try:
                return bool(await self._redis.get(f"ingestion_cancel:{tenant_id}:{job_id}"))
            except Exception as exc:
                _log.warning("ingestion_cancel_check_failed job=%s: %s", job_id, exc)
                return False
        return job_id in self._cancelled

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

    async def complete_job(
        self,
        job: IngestionJob,
        *,
        error: str = "",
        cancelled: bool = False,
        partial: bool = False,
        notices: list[str] | None = None,
    ) -> None:
        """Finish the job: ``completed``, ``partial``, ``failed`` or ``cancelled``.

        USR-1: a job with failures is never ``completed``. A sync-level ``error``
        (connection / auth / listing failure) is ``failed`` and counts at least
        one failure; ``partial`` (some units of the source could not be read,
        the rest synced) is ``partial`` when anything was synced. Document
        failures without an error make the job ``partial`` — or ``failed`` when
        nothing was synced — with a message saying how many failed.
        """
        synced = job.docs_indexed + job.docs_skipped
        if error:
            job.docs_failed = max(job.docs_failed, 1)
            job.status = "partial" if partial and synced else "failed"
            job.error_message = error[:2048]
        elif cancelled:
            job.status = "cancelled"
            job.error_message = ""
        elif job.docs_failed:
            job.status = "partial" if synced else "failed"
            job.error_message = (
                f"{job.docs_failed} document(s) failed to sync; the failures are in the "
                "ingestion DLQ and are retried automatically"
            )
        else:
            job.status = "completed"
            job.error_message = ""
        if notices:
            # USR-5: things the tenant should act on (e.g. a URL that moved
            # permanently) are shown on the job even when it succeeded.
            text = "; ".join(notices)
            job.error_message = (f"{job.error_message}; {text}" if job.error_message else text)[
                :2048
            ]
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

    async def latest_job(self, source_id: str, tenant_id: str) -> IngestionJob | None:
        """The newest job of a source — from ``ingestion_jobs`` when DB-backed.

        Syncs run in the Celery worker, so this process's in-memory job list never
        holds them; reading it made ``GET /sources/{id}/sync/status`` report
        "never synced" after every sync. Read under the tenant's RLS context.
        """
        if self._db is None:
            jobs = [
                j for j in self._jobs.values()
                if j.source_id == source_id and j.tenant_id == tenant_id
            ]
            return max(jobs, key=lambda j: j.created_at) if jobs else None

        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                        SELECT id, source_id, tenant_id, status, sync_mode, triggered_by,
                               started_at, completed_at, docs_discovered, docs_indexed,
                               docs_skipped, docs_failed, chunks_created, bytes_processed,
                               tokens_consumed, cursor_before, cursor_after, error_message,
                               created_at
                          FROM ingestion_jobs
                         WHERE source_id = :source_id AND tenant_id = :tenant_id
                         ORDER BY created_at DESC
                         LIMIT 1
                    """),
                    {"source_id": source_id, "tenant_id": tenant_id},
                )
            ).mappings().first()
        if row is None:
            return None

        def _ts(value: Any) -> str | None:
            return value.isoformat() if value is not None else None

        return IngestionJob(
            job_id=str(row["id"]),
            source_id=str(row["source_id"]),
            tenant_id=str(row["tenant_id"]),
            status=str(row["status"]),
            sync_mode=str(row["sync_mode"]),
            triggered_by=str(row["triggered_by"] or ""),
            started_at=_ts(row["started_at"]),
            completed_at=_ts(row["completed_at"]),
            docs_discovered=int(row["docs_discovered"] or 0),
            docs_indexed=int(row["docs_indexed"] or 0),
            docs_skipped=int(row["docs_skipped"] or 0),
            docs_failed=int(row["docs_failed"] or 0),
            chunks_created=int(row["chunks_created"] or 0),
            bytes_processed=int(row["bytes_processed"] or 0),
            tokens_consumed=int(row["tokens_consumed"] or 0),
            cursor_before=str(row["cursor_before"] or ""),
            cursor_after=str(row["cursor_after"] or ""),
            error_message=str(row["error_message"] or ""),
            created_at=_ts(row["created_at"]) or "",
        )

    async def list_jobs(
        self, source_id: str, tenant_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """A source's sync jobs, newest first (``ingestion_jobs`` under tenant RLS).

        Syncs run in Celery workers, so this process's in-memory job map only
        knows jobs it ran itself; with a DB the table is the history.
        """
        import dataclasses

        if self._db is None:
            jobs = [
                j for j in self._jobs.values()
                if j.source_id == source_id and j.tenant_id == tenant_id
            ]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            return [dataclasses.asdict(j) for j in jobs[:limit]]
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT id AS job_id, source_id, tenant_id, status, sync_mode, "
                        "triggered_by, started_at, completed_at, docs_discovered, "
                        "docs_indexed, docs_skipped, docs_failed, chunks_created, "
                        "bytes_processed, tokens_consumed, cursor_before, cursor_after, "
                        "error_message, created_at FROM ingestion_jobs "
                        "WHERE source_id = :sid AND tenant_id = :tid "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"sid": source_id, "tid": tenant_id, "lim": limit},
                )
            ).mappings().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("started_at", "completed_at", "created_at"):
                value = item.get(key)
                item[key] = value.isoformat() if isinstance(value, datetime) else value
            out.append(item)
        return out

    # ── DB persistence (no-ops when DB not available) ─────────────────────────
    #
    # Every write below is tenant work: it runs on the application factory, in a
    # transaction with the ``app.tenant_id`` GUC set, and carries an explicit
    # ``tenant_id`` predicate too. ingestion_jobs / ingestion_dlq /
    # source_configs are all FORCE ROW LEVEL SECURITY, so under the NOBYPASSRLS
    # application role a statement without the GUC is rejected (INSERT) or
    # matches zero rows (UPDATE). These sites used to open bare sessions, and the
    # failure was swallowed at DEBUG — every job row the manual and scheduled
    # sync "recorded" silently never existed.

    async def _persist_job_created(self, job: IngestionJob) -> None:
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, job.tenant_id),
            ):
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
            _log.warning("ingestion_job_persist_error job=%s: %s", job.job_id, e)

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
                         WHERE id = :job_id AND tenant_id = :tenant_id
                    """),
                    {"cursor": cursor, "job_id": job_id, "tenant_id": tenant_id},
                )
        except Exception as e:
            _log.warning("ingestion_cursor_persist_error source=%s: %s", source_id, e)

    async def _persist_job_completed(self, job: IngestionJob) -> None:
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, job.tenant_id),
            ):
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                           SET status = :status,
                               completed_at = NOW(),
                               docs_indexed = :indexed,
                               docs_skipped = :skipped,
                               docs_failed = :failed,
                               chunks_created = :chunks,
                               docs_discovered = :discovered,
                               bytes_processed = :bytes,
                               tokens_consumed = :tokens,
                               error_message = :error,
                               cursor_after = :cursor
                         WHERE id = :job_id AND tenant_id = :tenant_id
                    """),
                    {
                        "status": job.status,
                        "indexed": job.docs_indexed,
                        "skipped": job.docs_skipped,
                        "failed": job.docs_failed,
                        "chunks": job.chunks_created,
                        # Tokens/bytes/discovered were tracked in memory but never
                        # persisted, so /ingestion/cost had nothing real to report.
                        "discovered": job.docs_discovered,
                        "bytes": job.bytes_processed,
                        "tokens": job.tokens_consumed,
                        "error": job.error_message,
                        "cursor": job.cursor_after,
                        "job_id": job.job_id,
                        "tenant_id": job.tenant_id,
                    },
                )
        except Exception as e:
            _log.warning("ingestion_job_complete_persist_error job=%s: %s", job.job_id, e)

    # ── Methods required by scheduler.py ────────────────────────────────────

    def _system_factory(self) -> Any:
        """Maintenance-role (BYPASSRLS) factory — for the cross-tenant beat scans
        (``get_due_sources``, ``get_retryable_dlq_entries``) ONLY, never for
        per-tenant work and never on a request path."""
        if self._system_db is not None:
            return self._system_db
        from app.db.session import get_system_session_factory

        return get_system_session_factory()

    async def reap_stale_jobs(self, *, older_than_seconds: int) -> list[dict[str, Any]]:
        """Mark orphaned jobs failed: ``running``/``pending`` rows nobody finished.

        A worker killed mid-sync (deploy, OOM, node loss) never reaches
        ``complete_job``, so its row stayed ``running`` forever and the Sources
        UI showed a sync that would never end. Any job older than
        ``older_than_seconds`` — chosen above the source lock TTL, so a live
        sync still holding its lock is never touched — is failed with an honest
        reason. Cross-tenant beat scan → the maintenance role. Returns the rows
        reaped (id, tenant_id, source_id).
        """
        if self._db is None and self._system_db is None:
            reaped: list[dict[str, Any]] = []
            cutoff = datetime.now(UTC).timestamp() - older_than_seconds
            for job in self._jobs.values():
                started = job.started_at or job.created_at
                if job.status in ("running", "pending") and started and (
                    datetime.fromisoformat(started).timestamp() < cutoff
                ):
                    job.status = "failed"
                    job.error_message = _orphan_reason(older_than_seconds)
                    job.completed_at = datetime.now(UTC).isoformat()
                    reaped.append(
                        {"id": job.job_id, "tenant_id": job.tenant_id, "source_id": job.source_id}
                    )
            return reaped
        from sqlalchemy import text

        async with (
            self._system_factory()() as session,
            session.begin(),
            system_session(session),
        ):
            result = await session.execute(
                text("""
                    UPDATE ingestion_jobs
                       SET status = 'failed',
                           completed_at = NOW(),
                           error_message = :reason
                     WHERE status IN ('running', 'pending')
                       AND COALESCE(started_at, created_at)
                           < NOW() - make_interval(secs => :age)
                 RETURNING id, tenant_id, source_id
                """),
                {"reason": _orphan_reason(older_than_seconds), "age": older_than_seconds},
            )
            rows = [dict(row) for row in result.mappings()]
        for row in rows:
            job = self._jobs.get(str(row["id"]))
            if job is not None:
                job.status = "failed"
                job.error_message = _orphan_reason(older_than_seconds)
        return rows

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
        if self._db is None:
            return None
        try:
            # One tenant's Source: per-tenant RLS via the durable store, which
            # owns the row → SourceConfig mapping. (This previously queried a
            # ``source_id`` column that source_configs has never had — its key is
            # ``id`` — and so always returned None.)
            from app.ingestion.source_store import SourceConfigStore

            return await SourceConfigStore(db=self._db).get(source_id, tenant_id)
        except Exception as exc:
            _log.warning("load_config_error source=%s: %s", source_id, exc)
        return None

    async def get_due_sources(self) -> list[tuple[str, str]]:
        """Return list of (source_id, tenant_id) tuples whose next sync is due.

        Cross-tenant beat scan: delegates to ``SourceConfigStore.list_due`` on the
        maintenance-role factory (one implementation of the scan, one bound).
        """
        if self._db is None and self._system_db is None:
            return []
        try:
            from app.ingestion.source_store import SourceConfigStore

            return await SourceConfigStore(system_db=self._system_factory()).list_due()
        except Exception as exc:
            _log.warning("get_due_sources_error: %s", exc)
            return []

    async def increment_failure_counter(self, source_id: str, tenant_id: str) -> None:
        """Increment consecutive failure counter on a source."""
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                # source_configs is keyed by ``id``; the old ``WHERE source_id``
                # named a column that does not exist, so this never ran.
                await session.execute(
                    text(
                        "UPDATE source_configs "
                        "SET consecutive_failures = COALESCE(consecutive_failures, 0) + 1 "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": source_id, "tid": tenant_id},
                )
        except Exception as exc:
            _log.warning("increment_failure_counter_error source=%s: %s", source_id, exc)

    async def reset_failure_counter(self, source_id: str, tenant_id: str) -> None:
        """Reset consecutive failure counter after a successful sync."""
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE source_configs SET consecutive_failures = 0 "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": source_id, "tid": tenant_id},
                )
        except Exception as exc:
            _log.warning("reset_failure_counter_error source=%s: %s", source_id, exc)

    async def add_to_dlq(
        self,
        *,
        source_id: str,
        tenant_id: str,
        doc_id: str,
        error: str,
        raw_doc: object,
        job_id: str | None = None,
        failed_stage: str = "pipeline",
        failure_type: str = "pipeline_failure",
    ) -> None:
        """Add a failed document to the DLQ.

        ``raw_doc`` is serialized into ``raw_doc_json`` so the entry carries the
        payload needed to retry (e.g. the repo-ingest parameters). It may be a
        dataclass, a pydantic model, a mapping, or anything JSON-serializable;
        non-serializable values fall back to a minimal ``{"doc_id": ...}`` record.
        ``bytes`` (a ``RawDocument``'s content) are base64-encoded so the retry
        job can rebuild the document exactly (``raw_document_from_dlq_json``).

        The INSERT supplies every NOT NULL column of ``ingestion_dlq`` (``id``,
        ``failed_stage``, ``failure_type``) — it used to omit all three, so it
        could never succeed — and runs under the row's tenant RLS context.
        """
        if self._db is None:
            return
        import dataclasses as _dc
        import uuid as _uuid

        def _serialize(obj: object) -> str:
            payload: object = {"doc_id": doc_id}
            if obj is not None:
                if _dc.is_dataclass(obj) and not isinstance(obj, type):
                    payload = _dc.asdict(obj)
                elif hasattr(obj, "model_dump"):
                    payload = obj.model_dump()
                elif isinstance(obj, dict):
                    payload = obj
            try:
                return json.dumps(payload, default=_json_default)
            except (TypeError, ValueError):
                return json.dumps({"doc_id": doc_id})

        dlq_id = str(_uuid.uuid4())
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO ingestion_dlq
                            (id, dlq_id, source_id, tenant_id, job_id, doc_id,
                             failed_stage, failure_type, error_message,
                             raw_doc_json, retry_count, created_at)
                        VALUES
                            (:dlq_id, :dlq_id, :source_id, :tenant_id, :job_id, :doc_id,
                             :failed_stage, :failure_type, :error,
                             :raw_doc_json, 0, NOW())
                    """),
                    {
                        "dlq_id": dlq_id,
                        "source_id": source_id,
                        "tenant_id": tenant_id,
                        "job_id": job_id,
                        "doc_id": doc_id,
                        "failed_stage": (failed_stage or "pipeline")[:32],
                        "failure_type": (failure_type or "pipeline_failure")[:32],
                        "error": error,
                        "raw_doc_json": _serialize(raw_doc),
                    },
                )
        except Exception as exc:
            _log.warning("add_to_dlq_error source=%s doc=%s: %s", source_id, doc_id, exc)

    async def get_retryable_dlq_entries(self, max_entries: int = 50) -> list[dict[str, Any]]:
        """Return unresolved, non-permanent DLQ entries, oldest first.

        Cross-tenant beat scan → maintenance role (``system_session`` on the
        system factory). Under the application role it matched zero rows — and in
        the worker, the tracker it ran on had no DB at all — so the retry job
        never saw an entry. Entries at or past the retry cap are included so the
        caller can flag them permanent instead of leaving them stranded. Every
        per-entry follow-up (``resolve_dlq_entry`` …) is tenant-scoped.
        """
        if self._db is None and self._system_db is None:
            return []
        try:
            from sqlalchemy import text

            async with (
                self._system_factory()() as session,
                session.begin(),
                system_session(session),
            ):
                result = await session.execute(
                    text("""
                        SELECT id AS dlq_id, tenant_id, source_id, job_id, doc_id,
                               error_message, raw_doc_json, retry_count
                          FROM ingestion_dlq
                         WHERE permanent_failure IS NOT TRUE
                           AND resolved_at IS NULL
                           AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                           -- Entries of a parked Source (L-02) wait for its fix
                           -- instead of filling every batch.
                           AND NOT EXISTS (
                               SELECT 1 FROM source_configs sc
                                WHERE sc.id = ingestion_dlq.source_id
                                  AND sc.tenant_id = ingestion_dlq.tenant_id
                                  AND sc.config_status <> 'ok'
                           )
                         ORDER BY created_at ASC
                         LIMIT :limit
                    """),
                    {"limit": max_entries},
                )
                return [dict(row) for row in result.mappings()]
        except Exception as exc:
            _log.warning("get_retryable_dlq_entries_error: %s", exc)
            return []

    # ── Tenant read models (GET /ingestion/dlq, GET /ingestion/cost) ────────

    async def list_dlq_entries(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        source_id: str = "",
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        """One tenant's DLQ rows, newest first, under its RLS context.

        ``GET /ingestion/dlq`` returned a hardcoded ``[]`` while this table was
        being populated. Served by ``idx_ingestion_dlq_tenant_open``. Raises
        when no DB is wired or the query fails (the API answers 5xx) — an empty
        list must mean "no entries", never "could not look".
        """
        if self._db is None:
            raise RuntimeError("ingestion DLQ requires a database")
        from sqlalchemy import text

        sql = (
            "SELECT id, source_id, job_id, doc_id, failed_stage, failure_type, "
            "error_message, last_error, retry_count, next_retry_at, last_retried_at, "
            "COALESCE(permanent_failure, false) AS permanent_failure, resolved_at, "
            "created_at FROM ingestion_dlq WHERE tenant_id = :tid"
        )
        params: dict[str, Any] = {"tid": tenant_id, "limit": limit}
        if not include_resolved:
            sql += " AND resolved_at IS NULL"
        if source_id:
            sql += " AND source_id = :sid"
            params["sid"] = source_id
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (await session.execute(text(sql), params)).mappings().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("next_retry_at", "last_retried_at", "resolved_at", "created_at"):
                value = item.get(key)
                item[key] = value.isoformat() if isinstance(value, datetime) else value
            out.append(item)
        return out

    async def monthly_usage(self, tenant_id: str) -> dict[str, int]:
        """This calendar month's ingestion-job totals for one tenant (UTC).

        Aggregate over ``ingestion_jobs`` via ``idx_ingestion_jobs_tenant_created``.
        Raises when no DB is wired or the query fails.
        """
        if self._db is None:
            raise RuntimeError("ingestion usage requires a database")
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT COUNT(*), COALESCE(SUM(tokens_consumed), 0), "
                        "COALESCE(SUM(docs_indexed), 0), COALESCE(SUM(chunks_created), 0), "
                        "COALESCE(SUM(bytes_processed), 0) FROM ingestion_jobs "
                        "WHERE tenant_id = :tid "
                        "AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') "
                        "AT TIME ZONE 'UTC'"
                    ),
                    {"tid": tenant_id},
                )
            ).one()
        return {
            "jobs": int(row[0] or 0),
            "tokens": int(row[1] or 0),
            "docs_indexed": int(row[2] or 0),
            "chunks_created": int(row[3] or 0),
            "bytes_processed": int(row[4] or 0),
        }

    async def get_dlq_entry(self, dlq_id: str, tenant_id: str) -> dict[str, Any] | None:
        """One of the tenant's DLQ rows (for an operator retry), or None.

        Raises when no DB is wired or the query fails.
        """
        if self._db is None:
            raise RuntimeError("ingestion DLQ requires a database")
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT id AS dlq_id, tenant_id, source_id, job_id, doc_id, "
                        "error_message, raw_doc_json, retry_count, resolved_at, "
                        "COALESCE(permanent_failure, false) AS permanent_failure "
                        "FROM ingestion_dlq WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": dlq_id, "tid": tenant_id},
                )
            ).mappings().first()
        return dict(row) if row is not None else None

    async def resolve_dlq_entry(self, dlq_id: str, tenant_id: str) -> None:
        """Mark a DLQ entry as resolved (successfully retried).

        Sets ``resolved_at`` rather than deleting, so the failure history stays
        auditable; the retry scan skips resolved rows.
        """
        await self._update_dlq_entry(
            "UPDATE ingestion_dlq SET resolved_at = NOW() "
            "WHERE id = :id AND tenant_id = :tid",
            {"id": dlq_id, "tid": tenant_id},
            tenant_id=tenant_id,
            op="resolve_dlq_entry",
        )

    async def increment_dlq_retry(self, dlq_id: str, tenant_id: str, error: str = "") -> None:
        """Increment retry count on a DLQ entry."""
        # Exponential backoff (5 min x 2^attempt, capped at 6 h) recorded in
        # next_retry_at, which the retry scan honours — without it every beat
        # tick re-picked the same oldest failing rows, starving newer entries.
        await self._update_dlq_entry(
            "UPDATE ingestion_dlq SET retry_count = retry_count + 1, "
            "last_error = :error, last_retried_at = NOW(), "
            "next_retry_at = NOW() + LEAST(interval '6 hours', "
            "interval '5 minutes' * power(2, retry_count)) "
            "WHERE id = :id AND tenant_id = :tid",
            {"id": dlq_id, "tid": tenant_id, "error": error},
            tenant_id=tenant_id,
            op="increment_dlq_retry",
        )

    async def mark_dlq_permanent_failure(self, dlq_id: str, tenant_id: str) -> None:
        """Mark a DLQ entry as permanently failed (no more retries)."""
        await self._update_dlq_entry(
            "UPDATE ingestion_dlq SET permanent_failure = true "
            "WHERE id = :id AND tenant_id = :tid",
            {"id": dlq_id, "tid": tenant_id},
            tenant_id=tenant_id,
            op="mark_dlq_permanent_failure",
        )

    async def _update_dlq_entry(
        self, sql: str, params: dict[str, Any], *, tenant_id: str, op: str
    ) -> None:
        """Run one per-entry DLQ UPDATE under the entry's tenant RLS context.

        The retry job found the entry with a cross-tenant scan, but acting on it
        is one tenant's work — so it runs least-privilege on the application
        factory, not on the maintenance role.
        """
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(text(sql), params)
        except Exception as exc:
            _log.warning("%s_error dlq=%s: %s", op, params.get("id"), exc)


def _json_default(obj: object) -> object:
    """``json.dumps`` fallback: bytes round-trip as base64, anything else as str."""
    if isinstance(obj, bytes | bytearray):
        return {_BYTES_MARKER: base64.b64encode(bytes(obj)).decode("ascii")}
    return str(obj)


def raw_document_from_dlq_json(
    raw_doc_json: str | None, *, source_id: str, tenant_id: str, doc_id: str = ""
) -> RawDocument | None:
    """Rebuild the ``RawDocument`` a DLQ row was written for, or None.

    Returns None when the payload is not a connector document (e.g. the
    repo-ingest parameters) — there is nothing the pipeline can replay. The
    row's own ``tenant_id`` / ``source_id`` always win over whatever the JSON
    says: the payload is data, the row is the authority.
    """
    import ast
    import dataclasses as _dc

    if not raw_doc_json:
        return None
    try:
        payload = json.loads(raw_doc_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "content" not in payload:
        return None

    content = payload["content"]
    if isinstance(content, dict) and _BYTES_MARKER in content:
        try:
            content = base64.b64decode(content[_BYTES_MARKER], validate=True)
        except (TypeError, ValueError):
            return None
    elif isinstance(content, str):
        # Rows written before bytes were base64-encoded hold ``str(b"...")``.
        decoded: object = None
        if content[:2] in ("b'", 'b"'):
            try:
                decoded = ast.literal_eval(content)
            except (ValueError, SyntaxError):
                decoded = None
        content = decoded if isinstance(decoded, bytes) else content.encode("utf-8")
    else:
        return None

    names = {f.name for f in _dc.fields(RawDocument)}
    kwargs: dict[str, Any] = {k: v for k, v in payload.items() if k in names}
    kwargs["content"] = content
    kwargs["source_id"] = source_id
    kwargs["tenant_id"] = tenant_id
    kwargs["doc_id"] = str(kwargs.get("doc_id") or doc_id)
    kwargs.setdefault("content_type", "application/octet-stream")
    try:
        return RawDocument(**kwargs)
    except (TypeError, ValueError):
        return None
