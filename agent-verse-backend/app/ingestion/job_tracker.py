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

import asyncio
import base64
import contextlib
import dataclasses
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context, system_session
from app.ingestion.source_config import IngestionJob, RawDocument, SourceConfig

# Marks base64-encoded bytes inside a DLQ ``raw_doc_json`` payload.
_BYTES_MARKER = "__bytes_b64__"

_log = logging.getLogger(__name__)


class SyncLockUnavailableError(RuntimeError):
    """The shared sync lock cannot be taken or checked (Redis unreachable)."""


class SyncLockLostError(RuntimeError):
    """This run no longer holds its Source's sync lock (expired, or taken over).

    Raised by the lease check between documents and by a fenced cursor commit
    that matched no row; the run stops without committing anything more.
    """


class IngestionPersistenceError(RuntimeError):
    """A job row or cursor could not be written to Postgres (NF-12).

    These writes used to be logged at WARNING and dropped, so a job that was
    never recorded — or a cursor that was never committed — looked successful.
    The run stops instead; a cursor is never advanced past a failed write.
    """


def _db_error(exc: BaseException) -> str:
    """The error class only: driver messages can name hosts/ports (tenant-visible)."""
    return type(exc).__name__


# Hold KEYS[1] under ARGV[1] for ARGV[2] ms: extend it if ours, take it if free.
_ADOPT_LUA = """
local v = redis.call('GET', KEYS[1])
if v == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
end
if not v then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
  return 1
end
return 0
"""
# A run claims the lock queued under ARGV[1] (or a free one) and rotates its
# value to its own attempt token ARGV[2] for ARGV[3] ms (SYNC-ORPHAN): a second
# delivery of the same task message (a broker redelivery while the first run is
# alive) then finds the attempt token, not the queued one, and stands down.
_CLAIM_LUA = """
local v = redis.call('GET', KEYS[1])
if v == ARGV[1] or not v then
  redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
  return 1
end
return 0
"""
# Extend KEYS[1] only while ARGV[1] holds it.
_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
# Delete KEYS[1] only while ARGV[1] holds it.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


# A running sync's lock value is ``<job id>#<attempt nonce>``; a queued one (taken
# by the API, the scheduler or orphan recovery) is the bare job id.
ATTEMPT_SEPARATOR = "#"


def lock_job_id(lock_value: str) -> str:
    """The job id a sync lock value names (queued token or ``job#attempt``)."""
    return str(lock_value).split(ATTEMPT_SEPARATOR, 1)[0]


def new_attempt_token(job_id: str) -> str:
    return f"{job_id}{ATTEMPT_SEPARATOR}{uuid.uuid4().hex[:12]}"


# Statuses of a job that is still owned by a (queued or running) sync.
ACTIVE_JOB_STATUSES = ("pending", "running")


class SyncLease:
    """A held Source sync lock: its token, its fencing token, and its renewal.

    A background task renews the lock every third of its TTL. When a renewal
    finds the lock gone (or Redis has not confirmed it for a whole TTL) the
    lease is ``lost`` and :meth:`check` raises :class:`SyncLockLostError`, so
    the sync stops between documents instead of running on unlocked.
    """

    def __init__(
        self,
        tracker: IngestionJobTracker,
        source_id: str,
        tenant_id: str,
        token: str,
        fence: int,
        ttl_seconds: int,
        *,
        job_id: str | None = None,
    ) -> None:
        self.tracker = tracker
        self.source_id = source_id
        self.tenant_id = tenant_id
        self.token = token
        # The job this run executes (the lock value's job part).
        self.job_id = job_id or lock_job_id(token)
        self.fence = fence
        self.ttl_seconds = ttl_seconds
        self.lost = False
        self.reason = ""
        self._task: asyncio.Task[None] | None = None
        # SYNC-ORPHAN: after each renewal the run's job row is heart-beaten
        # (``heartbeat_at``); False from it means the job was taken over.
        self._heartbeat: Callable[[], Awaitable[bool]] | None = None

    def attach_heartbeat(self, heartbeat: Callable[[], Awaitable[bool]]) -> None:
        """Beat the run's job row after every lock renewal (SYNC-ORPHAN).

        The heartbeat returns False when the row is no longer this run's (orphan
        recovery requeued it): the lease is then lost and the run stops. A
        heartbeat that cannot be written (a DB blip) is logged and retried at the
        next renewal -- the Redis lock, still renewed, keeps the run alive.
        """
        self._heartbeat = heartbeat

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._renew_forever())

    async def _renew_forever(self) -> None:
        interval = max(0.05, self.ttl_seconds / 3)
        confirmed = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            try:
                held = await self.tracker.renew_lock(
                    self.source_id, self.tenant_id, self.token, self.ttl_seconds
                )
            except Exception as exc:
                _log.warning("ingestion_lock_renew_error source=%s: %s", self.source_id, exc)
                if time.monotonic() - confirmed >= self.ttl_seconds:
                    self._lose(f"the lock could not be renewed for {self.ttl_seconds}s: {exc}")
                    return
                continue
            if not held:
                self._lose("the lock expired or was taken by another sync")
                return
            confirmed = time.monotonic()
            if self._heartbeat is not None:
                try:
                    owned = await self._heartbeat()
                except Exception as exc:
                    _log.warning(
                        "ingestion_job_heartbeat_error source=%s job=%s: %s",
                        self.source_id, self.job_id, exc,
                    )
                    continue
                if owned is False:
                    self._lose("its job was requeued by orphan recovery")
                    return

    def _lose(self, reason: str) -> None:
        self.lost = True
        self.reason = reason
        _log.warning("ingestion_lock_lost source=%s job=%s: %s", self.source_id, self.token, reason)

    def check(self) -> None:
        if self.lost:
            raise SyncLockLostError(
                f"sync of source {self.source_id} stopped: it no longer holds the source's "
                f"sync lock ({self.reason}); nothing more was committed"
            )

    async def release(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self.tracker.release_lock(self.source_id, self.tenant_id, self.token)


def _orphan_reason(older_than_seconds: int) -> str:
    return (
        f"orphaned: no worker finished this job within {older_than_seconds}s "
        "(the worker was lost or restarted); trigger the sync again"
    )


# ── Worker-loss recovery (SYNC-ORPHAN): claim plan of a sync run's job ─────────

# The job columns served by the API (never the lease token).
_JOB_COLUMNS = (
    "id, source_id, tenant_id, status, sync_mode, triggered_by, started_at, completed_at, "
    "docs_discovered, docs_indexed, docs_skipped, docs_failed, chunks_created, "
    "bytes_processed, tokens_consumed, cursor_before, cursor_after, error_message, "
    "created_at, attempts, requeue_reason, heartbeat_at"
)

REDELIVERED_REASON = "redelivered after its worker was lost"


def superseded_message(job_id: str) -> str:
    return (
        "worker lost: this sync's worker stopped heart-beating; superseded by sync job "
        f"{job_id}, which resumes from the last checkpoint"
    )


def resumes_reason(previous_job_id: str) -> str:
    return f"resumes sync job {previous_job_id} (its worker was lost)"


def exhausted_message(runs: int, reason: str) -> str:
    detail = f" ({reason})" if reason else ""
    return (
        f"gave up after {runs} attempt(s): the sync's worker was lost each time{detail}; "
        "the next scheduled sync resumes from the last checkpoint"
    )


@dataclasses.dataclass(frozen=True)
class JobClaimPlan:
    """What a sync run does with its job row (see :func:`plan_job_claim`)."""

    action: str  # "insert" (first run) | "resume" (existing active row) | "finished"
    attempts: int = 1
    reason: str = ""
    superseded: tuple[str, ...] = ()
    exhausted: bool = False
    error: str = ""


def plan_job_claim(
    *,
    existing: dict[str, Any] | None,
    others: list[dict[str, Any]],
    job_id: str,
    max_attempts: int | None,
    inherit_attempts: bool,
) -> JobClaimPlan:
    """Decide how a run that now holds the Source's lock claims job ``job_id``.

    * A finished job (completed / failed / cancelled ...) is never run again -- a
      task message delivered after its job ended does nothing.
    * An active row requeued by orphan recovery carries the bare job id as its
      lease token; recovery already counted that attempt. Any other active row
      (a previous attempt's token) is a broker redelivery after its worker died:
      one more attempt.
    * A first run continues the attempt count of the Source's other active jobs
      when ``inherit_attempts``: they are dead (this run holds the lock), and a
      scheduled re-run of a sync that keeps killing its worker must stay bounded.
    * Past ``max_attempts`` the job is failed honestly instead of run.
    """
    superseded = tuple(sorted(str(o["id"]) for o in others))
    if existing is not None:
        if str(existing.get("status")) not in ACTIVE_JOB_STATUSES:
            return JobClaimPlan(action="finished")
        attempts = int(existing.get("attempts") or 1)
        reason = str(existing.get("requeue_reason") or "")
        if str(existing.get("lease_token") or "") != job_id:
            attempts += 1
            reason = REDELIVERED_REASON
        action = "resume"
    else:
        attempts, reason, action = 1, "", "insert"
        if others and inherit_attempts:
            previous = max(others, key=lambda o: (int(o.get("attempts") or 1), str(o["id"])))
            attempts = int(previous.get("attempts") or 1) + 1
            reason = resumes_reason(str(previous["id"]))
    if max_attempts is not None and attempts > max_attempts:
        return JobClaimPlan(
            action=action,
            attempts=attempts - 1,
            reason=reason,
            superseded=superseded,
            exhausted=True,
            error=exhausted_message(attempts - 1, reason),
        )
    return JobClaimPlan(action=action, attempts=attempts, reason=reason, superseded=superseded)


def _job_claim_row(job: IngestionJob) -> dict[str, Any]:
    return {
        "status": job.status,
        "attempts": job.attempts,
        "lease_token": job.lease_token,
        "requeue_reason": job.requeue_reason,
    }


def _orphan_candidate(job: IngestionJob, age: float) -> dict[str, Any]:
    return {
        "id": job.job_id,
        "tenant_id": job.tenant_id,
        "source_id": job.source_id,
        "status": job.status,
        "triggered_by": job.triggered_by,
        "attempts": job.attempts,
        "lease_token": job.lease_token,
        "cursor_after": job.cursor_after,
        "requeue_reason": job.requeue_reason,
        "heartbeat_age_s": age,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _job_from_row(row: Any) -> IngestionJob:
    """An :class:`IngestionJob` from an ``ingestion_jobs`` row (``_JOB_COLUMNS``)."""
    return IngestionJob(
        job_id=str(row["id"]),
        source_id=str(row["source_id"]),
        tenant_id=str(row["tenant_id"]),
        status=str(row["status"]),
        sync_mode=str(row["sync_mode"]),
        triggered_by=str(row["triggered_by"] or ""),
        started_at=_iso(row["started_at"]),
        completed_at=_iso(row["completed_at"]),
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
        created_at=_iso(row["created_at"]) or "",
        attempts=int(row.get("attempts") or 1),
        requeue_reason=str(row.get("requeue_reason") or ""),
        heartbeat_at=_iso(row.get("heartbeat_at")),
        lease_token=str(row.get("lease_token") or ""),
    )


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
        self._fences: dict[str, int] = {}  # source_id → last fence (no DB)

    # ── Distributed locking (LAW-14 / TG-12) ─────────────────────────────────
    #
    # One sync per Source across every API replica and Celery worker: the lock
    # is a Redis key (``SET NX`` + TTL) whose value is the holder's token (the
    # job id). Renewal, adoption and release are compare-and-set Lua scripts,
    # so a holder can only extend or free its OWN lock. With Redis configured,
    # a Redis failure refuses the lock (SyncLockUnavailableError) — it never
    # falls back to this process's memory, which another replica cannot see.
    #
    # A lock can still be lost (TTL expired while a worker was stalled). The
    # fencing token makes that harmless: each run that takes a Source's lock
    # bumps ``source_configs.sync_fence`` and gets the new value; its cursor
    # commits only apply while the row still carries that value, so a stale
    # holder's write matches nothing and it stops (SyncLockLostError).

    @staticmethod
    def _lock_key(tenant_id: str, source_id: str) -> str:
        return f"ingestion_lock:{tenant_id}:{source_id}"

    async def acquire_lock(
        self, source_id: str, tenant_id: str, ttl_seconds: int = 3600
    ) -> str | None:
        """Take the Source's sync lock; its token (the job id), or None when held.

        Raises :class:`SyncLockUnavailableError` when the shared Redis cannot
        answer (fail closed: "already running" would be a lie, and a
        process-local lock is no lock across replicas).
        """
        job_id = uuid.uuid4().hex
        if self._redis is not None:
            lock_key = self._lock_key(tenant_id, source_id)
            try:
                acquired = await self._redis.set(lock_key, job_id, nx=True, ex=ttl_seconds)
                if not acquired:
                    _log.info(
                        "ingestion_lock_held source=%s existing_job=%s",
                        source_id,
                        _text(await self._redis.get(lock_key)),
                    )
                    return None
                return job_id
            except Exception as exc:
                _log.warning("ingestion_lock_redis_error source=%s: %s", source_id, exc)
                raise SyncLockUnavailableError(
                    f"the sync lock for source {source_id} is unavailable: {exc}"
                ) from exc

        # Process-local (no Redis configured: a single-process dev setup).
        if source_id in self._locks:
            return None
        self._locks[source_id] = job_id
        return job_id

    async def adopt_lock(
        self, source_id: str, tenant_id: str, token: str, ttl_seconds: int
    ) -> bool:
        """Hold the lock under ``token`` for ``ttl_seconds`` more: True when it is ours.

        A worker adopts the lock the API took for its job. If that lock expired
        while the task waited in the queue and nobody took it, the worker takes
        it again under the same token; if another run holds it, False.
        """
        if self._redis is not None:
            try:
                result = await self._redis.eval(
                    _ADOPT_LUA,
                    1,
                    self._lock_key(tenant_id, source_id),
                    token,
                    str(int(ttl_seconds * 1000)),
                )
            except Exception as exc:
                raise SyncLockUnavailableError(
                    f"the sync lock for source {source_id} is unavailable: {exc}"
                ) from exc
            return int(result) == 1
        holder = self._locks.get(source_id)
        if holder is None:
            self._locks[source_id] = token
            return True
        return holder == token

    async def claim_lock(
        self, source_id: str, tenant_id: str, token: str, attempt_token: str, ttl_seconds: int
    ) -> bool:
        """Claim the lock queued under ``token`` (or a free one) as ``attempt_token``.

        True when this run now holds it. A lock already rotated to another
        attempt token -- the same job's message delivered twice, or another run
        -- is not taken.
        """
        if self._redis is not None:
            try:
                result = await self._redis.eval(
                    _CLAIM_LUA,
                    1,
                    self._lock_key(tenant_id, source_id),
                    token,
                    attempt_token,
                    str(int(ttl_seconds * 1000)),
                )
            except Exception as exc:
                raise SyncLockUnavailableError(
                    f"the sync lock for source {source_id} is unavailable: {exc}"
                ) from exc
            return int(result) == 1
        holder = self._locks.get(source_id)
        if holder is None or holder == token:
            self._locks[source_id] = attempt_token
            return True
        return False

    async def lock_holder(self, source_id: str, tenant_id: str) -> str | None:
        """The raw value of the Source's sync lock (None when free)."""
        if self._redis is not None:
            value = await self._redis.get(self._lock_key(tenant_id, source_id))
            return None if value is None else _text(value)
        return self._locks.get(source_id)

    async def queue_lock(
        self, source_id: str, tenant_id: str, job_id: str, *, ttl_seconds: int
    ) -> bool:
        """Take the free lock under the bare ``job_id`` (a requeued job waits on it)."""
        if self._redis is not None:
            return bool(
                await self._redis.set(
                    self._lock_key(tenant_id, source_id), job_id, nx=True, ex=ttl_seconds
                )
            )
        if source_id in self._locks:
            return False
        self._locks[source_id] = job_id
        return True

    async def renew_lock(
        self, source_id: str, tenant_id: str, token: str, ttl_seconds: int
    ) -> bool:
        """Extend the lock's TTL if ``token`` still holds it; False when it was lost."""
        if self._redis is not None:
            result = await self._redis.eval(
                _RENEW_LUA,
                1,
                self._lock_key(tenant_id, source_id),
                token,
                str(int(ttl_seconds * 1000)),
            )
            return int(result) == 1
        return self._locks.get(source_id) == token

    async def release_lock(self, source_id: str, tenant_id: str, job_id: str | None = None) -> None:
        """Release the Source's lock — only if ``job_id`` (the token) still holds it.

        Without a token nothing is released from Redis: an unowned release could
        free a lock another run holds (the old behaviour), letting a second sync
        start alongside it.
        """
        if self._redis is not None:
            if not job_id:
                _log.warning("ingestion_lock_release_without_token source=%s", source_id)
                return
            try:
                await self._redis.eval(
                    _RELEASE_LUA, 1, self._lock_key(tenant_id, source_id), job_id
                )
            except Exception as exc:
                # The TTL frees it; nothing else may.
                _log.warning("ingestion_lock_release_error source=%s: %s", source_id, exc)
            return
        if job_id:
            if self._locks.get(source_id) == job_id:
                del self._locks[source_id]
        else:
            self._locks.pop(source_id, None)

    async def running_job_id(self, source_id: str, tenant_id: str) -> str | None:
        """The job id holding the source's sync lock (a sync is running), or None."""
        value = await self.lock_holder(source_id, tenant_id)
        return None if value is None else lock_job_id(value)

    async def take_fence(self, source_id: str, tenant_id: str) -> int:
        """Issue this run's fencing token: bump and return ``source_configs.sync_fence``.

        Called once the run holds the lock. Any later holder bumps it again, so
        :meth:`update_cursor` with an older fence matches no row.
        """
        if self._db is None:
            self._fences[source_id] = self._fences.get(source_id, 0) + 1
            return self._fences[source_id]
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            fence = (
                await session.execute(
                    text(
                        "UPDATE source_configs SET sync_fence = sync_fence + 1 "
                        "WHERE id = :sid AND tenant_id = :tid RETURNING sync_fence"
                    ),
                    {"sid": source_id, "tid": tenant_id},
                )
            ).scalar_one_or_none()
        if fence is None:
            raise SyncLockLostError(f"source {source_id} no longer exists")
        return int(fence)

    async def hold(
        self, source_id: str, tenant_id: str, token: str, *, ttl_seconds: int
    ) -> SyncLease | None:
        """Claim the lock queued under ``token``, take a fence and keep the lock renewed.

        The lock's value becomes this run's own attempt token (``token#nonce``),
        so another delivery of the same queued task cannot hold it alongside
        this run (SYNC-ORPHAN). Returns None when another run holds the lock.
        The caller must :meth:`SyncLease.release` it (in a ``finally``).
        """
        attempt_token = new_attempt_token(token)
        if not await self.claim_lock(source_id, tenant_id, token, attempt_token, ttl_seconds):
            return None
        try:
            fence = await self.take_fence(source_id, tenant_id)
        except BaseException:
            await self.release_lock(source_id, tenant_id, attempt_token)
            raise
        lease = SyncLease(
            self, source_id, tenant_id, attempt_token, fence, ttl_seconds, job_id=token
        )
        lease.start()
        return lease

    async def hold_without_fence(
        self, source_id: str, tenant_id: str, *, ttl_seconds: int
    ) -> SyncLease | None:
        """Take the lock (new token) and keep it renewed — no fencing token (NF-18).

        For runs that commit no cursor and have no ``source_configs`` row to
        fence on (the webhook re-ingest of a synthetic source). None when the
        lock is held; raises :class:`SyncLockUnavailableError` when it cannot be
        checked. The caller must :meth:`SyncLease.release` it.
        """
        token = await self.acquire_lock(source_id, tenant_id, ttl_seconds=ttl_seconds)
        if token is None:
            return None
        lease = SyncLease(self, source_id, tenant_id, token, 0, ttl_seconds)
        lease.start()
        return lease

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
        lease_token: str | None = None,
        max_attempts: int | None = None,
        inherit_attempts: bool = True,
    ) -> IngestionJob:
        """Create a new ingestion job record -- or, with ``lease_token``, claim it.

        A sync run passes the attempt token it holds the Source's lock under
        (SYNC-ORPHAN). The job row is then claimed for this run: created on its
        first run, resumed (with its checkpointed counters) when the job was
        requeued after its worker died or its message was redelivered, and left
        alone when it already finished (the returned job is not ``running``; the
        caller must not run it). Other jobs of the Source still marked active
        are dead -- this run holds the lock -- and are superseded; a scheduled
        run continues their attempt count (``inherit_attempts``), so a Source
        whose syncs keep killing their worker is given up on after
        ``max_attempts``: the returned job is then ``failed`` with the reason.
        """
        if lease_token is not None:
            return await self._claim_job(
                source_config,
                job_id=job_id,
                triggered_by=triggered_by,
                lease_token=lease_token,
                max_attempts=max_attempts,
                inherit_attempts=inherit_attempts,
            )
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
        if self._db is not None:
            # Raises IngestionPersistenceError: an unrecorded job never runs.
            await self._persist_job_created(job)
        self._jobs[job_id] = job

        return job

    async def _claim_job(
        self,
        source_config: SourceConfig,
        *,
        job_id: str,
        triggered_by: str,
        lease_token: str,
        max_attempts: int | None,
        inherit_attempts: bool,
    ) -> IngestionJob:
        if self._db is not None:
            return await self._claim_job_db(
                source_config,
                job_id=job_id,
                triggered_by=triggered_by,
                lease_token=lease_token,
                max_attempts=max_attempts,
                inherit_attempts=inherit_attempts,
            )
        now = datetime.now(UTC).isoformat()
        existing = self._jobs.get(job_id)
        others = [
            {"id": j.job_id, "attempts": j.attempts}
            for j in self._jobs.values()
            if j.job_id != job_id
            and j.source_id == source_config.source_id
            and j.tenant_id == source_config.tenant_id
            and j.status in ACTIVE_JOB_STATUSES
            and j.lease_token
        ]
        plan = plan_job_claim(
            existing=None if existing is None else _job_claim_row(existing),
            others=others,
            job_id=job_id,
            max_attempts=max_attempts,
            inherit_attempts=inherit_attempts,
        )
        if plan.action == "finished":
            assert existing is not None
            return existing
        for other_id in plan.superseded:
            other = self._jobs[other_id]
            self._jobs[other_id] = dataclasses.replace(
                other,
                status="failed",
                completed_at=now,
                docs_failed=max(other.docs_failed, 1),
                error_message=superseded_message(job_id),
            )
        base = existing or IngestionJob(
            job_id=job_id,
            source_id=source_config.source_id,
            tenant_id=source_config.tenant_id,
            status="running",
            sync_mode=source_config.sync_mode,
            triggered_by=triggered_by,
            started_at=now,
            cursor_before=source_config.cursor_value or "",
            created_at=now,
        )
        job = dataclasses.replace(
            base,
            status="failed" if plan.exhausted else "running",
            attempts=plan.attempts,
            requeue_reason=plan.reason,
            lease_token=lease_token,
            heartbeat_at=now,
            started_at=base.started_at or now,
            completed_at=now if plan.exhausted else None,
            error_message=plan.error,
            docs_failed=max(base.docs_failed, 1) if plan.exhausted else base.docs_failed,
        )
        self._jobs[job_id] = job
        return job

    async def _claim_job_db(
        self,
        source_config: SourceConfig,
        *,
        job_id: str,
        triggered_by: str,
        lease_token: str,
        max_attempts: int | None,
        inherit_attempts: bool,
    ) -> IngestionJob:
        from sqlalchemy import text

        tenant_id = source_config.tenant_id
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            f"SELECT {_JOB_COLUMNS}, lease_token FROM ingestion_jobs "
                            "WHERE id = :id AND tenant_id = :tid FOR UPDATE"
                        ),
                        {"id": job_id, "tid": tenant_id},
                    )
                ).mappings().first()
                others = (
                    await session.execute(
                        text(
                            "SELECT id, attempts FROM ingestion_jobs "
                            "WHERE tenant_id = :tid AND source_id = :sid "
                            "AND status IN ('pending', 'running') "
                            "AND lease_token IS NOT NULL AND id <> :id FOR UPDATE"
                        ),
                        {"tid": tenant_id, "sid": source_config.source_id, "id": job_id},
                    )
                ).mappings().all()
                plan = plan_job_claim(
                    existing=None if row is None else dict(row),
                    others=[dict(o) for o in others],
                    job_id=job_id,
                    max_attempts=max_attempts,
                    inherit_attempts=inherit_attempts,
                )
                if plan.action == "finished":
                    assert row is not None
                    return _job_from_row(row)
                if plan.superseded:
                    await session.execute(
                        text(
                            "UPDATE ingestion_jobs SET status = 'failed', completed_at = NOW(), "
                            "docs_failed = GREATEST(docs_failed, 1), error_message = :msg "
                            "WHERE tenant_id = :tid AND id = ANY(:ids) "
                            "AND status IN ('pending', 'running')"
                        ),
                        {
                            "tid": tenant_id,
                            "ids": list(plan.superseded),
                            "msg": superseded_message(job_id),
                        },
                    )
                status = "failed" if plan.exhausted else "running"
                if row is None:
                    await session.execute(
                        text("""
                            INSERT INTO ingestion_jobs
                              (id, source_id, tenant_id, status, sync_mode, triggered_by,
                               cursor_before, started_at, created_at, attempts, lease_token,
                               heartbeat_at, requeue_reason, error_message, docs_failed,
                               completed_at)
                            VALUES
                              (:id, :source_id, :tenant_id, :status, :sync_mode, :triggered_by,
                               :cursor_before, NOW(), NOW(), :attempts, :lease_token,
                               NOW(), :reason, :error, :failed,
                               CASE WHEN :exhausted THEN NOW() END)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": job_id,
                            "source_id": source_config.source_id,
                            "tenant_id": tenant_id,
                            "status": status,
                            "sync_mode": source_config.sync_mode,
                            "triggered_by": triggered_by,
                            "cursor_before": source_config.cursor_value or "",
                            "attempts": plan.attempts,
                            "lease_token": lease_token,
                            "reason": plan.reason,
                            "error": plan.error,
                            "failed": 1 if plan.exhausted else 0,
                            "exhausted": plan.exhausted,
                        },
                    )
                else:
                    await session.execute(
                        text("""
                            UPDATE ingestion_jobs
                               SET status = :status,
                                   attempts = :attempts,
                                   lease_token = :lease_token,
                                   heartbeat_at = NOW(),
                                   requeue_reason = :reason,
                                   error_message = :error,
                                   started_at = COALESCE(started_at, NOW()),
                                   completed_at = CASE WHEN :exhausted THEN NOW() END,
                                   docs_failed = CASE WHEN :exhausted
                                                 THEN GREATEST(docs_failed, 1)
                                                 ELSE docs_failed END
                             WHERE id = :id AND tenant_id = :tid
                        """),
                        {
                            "status": status,
                            "attempts": plan.attempts,
                            "lease_token": lease_token,
                            "reason": plan.reason,
                            "error": plan.error,
                            "exhausted": plan.exhausted,
                            "id": job_id,
                            "tid": tenant_id,
                        },
                    )
        except Exception as e:
            _log.error("ingestion_job_claim_error job=%s: %s", job_id, e)
            raise IngestionPersistenceError(
                f"ingestion job {job_id} could not be recorded ({_db_error(e)})"
            ) from e
        now = datetime.now(UTC).isoformat()
        if row is not None:
            job = _job_from_row(row)
        else:
            job = IngestionJob(
                job_id=job_id,
                source_id=source_config.source_id,
                tenant_id=tenant_id,
                status="running",
                sync_mode=source_config.sync_mode,
                triggered_by=triggered_by,
                started_at=now,
                cursor_before=source_config.cursor_value or "",
                created_at=now,
            )
        job.status = status
        job.attempts = plan.attempts
        job.requeue_reason = plan.reason
        job.lease_token = lease_token
        job.heartbeat_at = now
        job.error_message = plan.error
        if plan.exhausted:
            job.completed_at = now
            job.docs_failed = max(job.docs_failed, 1)
        else:
            job.completed_at = None
        self._jobs[job_id] = job
        return job

    async def heartbeat_job(self, job: IngestionJob) -> bool:
        """Record that the run owning ``job`` is alive (SYNC-ORPHAN).

        False when the job row is no longer this run's: orphan recovery requeued
        it (judged its worker dead) or it was finished elsewhere.
        """
        if not job.lease_token:
            return True
        if self._db is None:
            current = self._jobs.get(job.job_id)
            if (
                current is None
                or current.lease_token != job.lease_token
                or current.status != "running"
            ):
                return False
            current.heartbeat_at = datetime.now(UTC).isoformat()
            return True
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, job.tenant_id),
        ):
            result = await session.execute(
                text(
                    "UPDATE ingestion_jobs SET heartbeat_at = NOW() "
                    "WHERE id = :id AND tenant_id = :tid AND lease_token = :lt "
                    "AND status = 'running'"
                ),
                {"id": job.job_id, "tid": job.tenant_id, "lt": job.lease_token},
            )
        return bool(getattr(result, "rowcount", 1))

    async def update_cursor(
        self,
        job: IngestionJob,
        new_cursor: str,
        source_config: SourceConfig,
        *,
        fence: int | None = None,
    ) -> None:
        """Commit the new cursor position — the key to resumability (LAW-03).

        Called after each batch of documents is successfully indexed.
        If the worker crashes after this point, the next run starts from here.

        With ``fence`` (the run's fencing token, TG-12) the commit applies only
        while the Source still carries that fence; otherwise a newer run owns the
        Source and :class:`SyncLockLostError` is raised — nothing is written.
        """
        if self._db is None:
            current = self._fences.get(source_config.source_id)
            if fence is not None and current is not None and current != fence:
                raise SyncLockLostError(
                    f"cursor of source {source_config.source_id} not committed: a newer "
                    "sync holds its lock"
                )
            record = self._jobs.get(job.job_id)
            if job.lease_token and record is not None and record.lease_token != job.lease_token:
                raise SyncLockLostError(
                    f"cursor of source {source_config.source_id} not committed: job "
                    f"{job.job_id} was requeued to another run"
                )
        if self._db is not None:
            await self._persist_cursor_update(
                source_config.source_id,
                source_config.tenant_id,
                new_cursor,
                job.job_id,
                fence=fence,
                job=job if job.lease_token else None,
            )
        job.cursor_after = new_cursor
        source_config.cursor_value = new_cursor

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
        if self._db is None and job.lease_token:
            record = self._jobs.get(job.job_id)
            if record is not None and (
                record.lease_token != job.lease_token or record.status not in ACTIVE_JOB_STATUSES
            ):
                _log.warning(
                    "ingestion_job_result_not_recorded job=%s: the job was taken over",
                    job.job_id,
                )
                return
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
            try:
                await self._persist_job_completed(job)
            except IngestionPersistenceError as exc:
                # Never report a result that was not recorded: the job is failed
                # here, the caller gets the error, and the stale-job reaper fails
                # the still-"running" row if no later write lands.
                job.status = "failed"
                job.error_message = str(exc)[:2048]
                raise

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
                    text(
                        f"SELECT {_JOB_COLUMNS} FROM ingestion_jobs "
                        "WHERE source_id = :source_id AND tenant_id = :tenant_id "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"source_id": source_id, "tenant_id": tenant_id},
                )
            ).mappings().first()
        return None if row is None else _job_from_row(row)

    async def list_jobs(
        self, source_id: str, tenant_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """A source's sync jobs, newest first (``ingestion_jobs`` under tenant RLS).

        Syncs run in Celery workers, so this process's in-memory job map only
        knows jobs it ran itself; with a DB the table is the history.
        """
        if self._db is None:
            jobs = [
                j for j in self._jobs.values()
                if j.source_id == source_id and j.tenant_id == tenant_id
            ]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            out_mem = [dataclasses.asdict(j) for j in jobs[:limit]]
            for item in out_mem:
                item.pop("lease_token", None)  # internal: the run's lock value
            return out_mem
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
                        "error_message, created_at, attempts, requeue_reason, heartbeat_at "
                        "FROM ingestion_jobs "
                        "WHERE source_id = :sid AND tenant_id = :tid "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"sid": source_id, "tid": tenant_id, "lim": limit},
                )
            ).mappings().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("started_at", "completed_at", "created_at", "heartbeat_at"):
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
            _log.error("ingestion_job_persist_error job=%s: %s", job.job_id, e)
            raise IngestionPersistenceError(
                f"ingestion job {job.job_id} could not be recorded ({_db_error(e)})"
            ) from e

    async def _persist_cursor_update(
        self,
        source_id: str,
        tenant_id: str,
        cursor: str,
        job_id: str,
        *,
        fence: int | None = None,
        job: IngestionJob | None = None,
    ) -> None:
        """Commit the cursor (fenced) and record it on the job.

        With ``job`` (a leased run, SYNC-ORPHAN) the job row also checkpoints its
        counters with the cursor -- a run requeued after its worker died resumes
        from both -- and is heart-beaten; the write applies only while the row is
        still this run's, else nothing is committed (SyncLockLostError).
        """
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                updated = await session.execute(
                    text(
                        "UPDATE source_configs SET cursor_value = :cursor, updated_at = NOW() "
                        "WHERE id = :source_id AND tenant_id = :tenant_id"
                        + ("" if fence is None else " AND sync_fence = :fence")
                    ),
                    {
                        "cursor": cursor,
                        "source_id": source_id,
                        "tenant_id": tenant_id,
                        **({} if fence is None else {"fence": fence}),
                    },
                )
                if fence is not None and getattr(updated, "rowcount", 1) == 0:
                    raise SyncLockLostError(
                        f"cursor of source {source_id} not committed: a newer sync holds "
                        "its lock (or the source was deleted)"
                    )
                if job is None:
                    await session.execute(
                        text("""
                            UPDATE ingestion_jobs
                               SET cursor_after = :cursor
                             WHERE id = :job_id AND tenant_id = :tenant_id
                        """),
                        {"cursor": cursor, "job_id": job_id, "tenant_id": tenant_id},
                    )
                else:
                    checkpoint = await session.execute(
                        text("""
                            UPDATE ingestion_jobs
                               SET cursor_after = :cursor,
                                   docs_discovered = :discovered,
                                   docs_indexed = :indexed,
                                   docs_skipped = :skipped,
                                   docs_failed = :failed,
                                   chunks_created = :chunks,
                                   tokens_consumed = :tokens,
                                   bytes_processed = :bytes,
                                   heartbeat_at = NOW()
                             WHERE id = :job_id AND tenant_id = :tenant_id
                               AND lease_token = :lease_token AND status = 'running'
                        """),
                        {
                            "cursor": cursor,
                            "discovered": job.docs_discovered,
                            "indexed": job.docs_indexed,
                            "skipped": job.docs_skipped,
                            "failed": job.docs_failed,
                            "chunks": job.chunks_created,
                            "tokens": job.tokens_consumed,
                            "bytes": job.bytes_processed,
                            "job_id": job_id,
                            "tenant_id": tenant_id,
                            "lease_token": job.lease_token,
                        },
                    )
                    if getattr(checkpoint, "rowcount", 1) == 0:
                        # Raised inside the transaction: the cursor is rolled back too.
                        raise SyncLockLostError(
                            f"cursor of source {source_id} not committed: job {job_id} was "
                            "requeued to another run"
                        )
        except SyncLockLostError:
            raise
        except Exception as e:
            _log.error("ingestion_cursor_persist_error source=%s: %s", source_id, e)
            raise IngestionPersistenceError(
                f"cursor of source {source_id} could not be saved ({_db_error(e)}); "
                "the sync stopped without advancing it"
            ) from e

    async def _persist_job_completed(self, job: IngestionJob) -> None:
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, job.tenant_id),
            ):
                result = await session.execute(
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
                    """ + (
                        # A leased run records its result only while the job is
                        # still its own (SYNC-ORPHAN): a run whose job was requeued
                        # -- or already finished -- must not overwrite it.
                        " AND lease_token = :lease_token AND status IN ('pending', 'running')"
                        if job.lease_token
                        else ""
                    )),
                    {
                        **({"lease_token": job.lease_token} if job.lease_token else {}),
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
                if job.lease_token and getattr(result, "rowcount", 1) == 0:
                    _log.warning(
                        "ingestion_job_result_not_recorded job=%s status=%s: the job was "
                        "requeued to another run or already finished",
                        job.job_id,
                        job.status,
                    )
        except Exception as e:
            _log.error("ingestion_job_complete_persist_error job=%s: %s", job.job_id, e)
            raise IngestionPersistenceError(
                f"result of ingestion job {job.job_id} could not be recorded ({_db_error(e)})"
            ) from e

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
                beat = job.heartbeat_at if job.lease_token else None
                if job.status in ("running", "pending") and started and (
                    datetime.fromisoformat(started).timestamp() < cutoff
                ) and (beat is None or datetime.fromisoformat(beat).timestamp() < cutoff):
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
                       -- A leased run that still heart-beats is alive, however
                       -- long it runs (SYNC-ORPHAN); dead leased runs are
                       -- recovered within minutes by recover_orphaned_syncs and
                       -- only reach this backstop when that recovery is off.
                       AND (lease_token IS NULL OR heartbeat_at IS NULL
                            OR heartbeat_at < NOW() - make_interval(secs => :age))
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

    # ── Worker-loss recovery (SYNC-ORPHAN) ────────────────────────────────────
    #
    # A leased sync run heart-beats its job row (``heartbeat_at``) every time it
    # renews the Source's lock. ``recover_orphaned_syncs`` (app.ingestion.
    # orphan_recovery) scans for active rows whose heartbeat went stale, checks
    # the lock, and requeues or gives up on each with a compare-and-set on the
    # row's lease token: a run that is in fact alive keeps its row.

    async def find_orphan_candidates(
        self, *, stale_seconds: float, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Active leased jobs whose heartbeat is older than ``stale_seconds``.

        Cross-tenant beat scan -> the maintenance role. Stalest first, bounded.
        """
        if self._db is None and self._system_db is None:
            now = datetime.now(UTC).timestamp()
            found: list[dict[str, Any]] = []
            for job in self._jobs.values():
                if job.status not in ACTIVE_JOB_STATUSES or not job.lease_token:
                    continue
                beat = job.heartbeat_at or job.started_at or job.created_at
                age = now - datetime.fromisoformat(beat).timestamp() if beat else 1e9
                if age >= stale_seconds:
                    found.append(_orphan_candidate(job, age))
            found.sort(key=lambda r: -float(r["heartbeat_age_s"]))
            return found[:limit]
        from sqlalchemy import text

        async with (
            self._system_factory()() as session,
            session.begin(),
            system_session(session),
        ):
            rows = (
                await session.execute(
                    text("""
                        SELECT id, tenant_id, source_id, status, triggered_by, attempts,
                               lease_token, cursor_after, requeue_reason,
                               EXTRACT(EPOCH FROM (NOW() - heartbeat_at)) AS heartbeat_age_s
                          FROM ingestion_jobs
                         WHERE status IN ('pending', 'running')
                           AND lease_token IS NOT NULL
                           AND heartbeat_at < NOW() - make_interval(secs => :stale)
                         ORDER BY heartbeat_at ASC
                         LIMIT :lim
                    """),
                    {"stale": float(stale_seconds), "lim": limit},
                )
            ).mappings().all()
        return [
            {
                **dict(r),
                "id": str(r["id"]),
                "attempts": int(r["attempts"] or 1),
                "cursor_after": str(r["cursor_after"] or ""),
                "heartbeat_age_s": float(r["heartbeat_age_s"] or 0.0),
            }
            for r in rows
        ]

    async def requeue_orphan(
        self, candidate: dict[str, Any], *, stale_seconds: float, reason: str
    ) -> int | None:
        """Hand an orphaned job to a new run: ``pending``, one more attempt.

        Compare-and-set on the lease token and attempt count read by the scan
        and on the heartbeat still being stale: a run that heart-beat (or was
        claimed) since keeps the row. The row's lease token becomes the bare job
        id -- the token the requeued task's lock waits under. Returns the new
        attempt number, or None when the row changed.
        """
        job_id, tenant_id = str(candidate["id"]), str(candidate["tenant_id"])
        if self._db is None:
            job = self._jobs.get(job_id)
            if job is None or not self._mem_still_orphaned(job, candidate, stale_seconds):
                return None
            now = datetime.now(UTC).isoformat()
            self._jobs[job_id] = dataclasses.replace(
                job,
                status="pending",
                attempts=job.attempts + 1,
                lease_token=job_id,
                heartbeat_at=now,
                requeue_reason=reason,
                error_message="",
            )
            return job.attempts + 1
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            attempts = (
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                           SET status = 'pending',
                               attempts = attempts + 1,
                               lease_token = :job_id,
                               heartbeat_at = NOW(),
                               requeue_reason = :reason,
                               error_message = ''
                         WHERE id = :job_id AND tenant_id = :tid
                           AND status IN ('pending', 'running')
                           AND lease_token = :lease_token AND attempts = :attempts
                           AND heartbeat_at < NOW() - make_interval(secs => :stale)
                     RETURNING attempts
                    """),
                    {
                        "job_id": job_id,
                        "tid": tenant_id,
                        "reason": reason[:2048],
                        "lease_token": str(candidate["lease_token"]),
                        "attempts": int(candidate["attempts"]),
                        "stale": float(stale_seconds),
                    },
                )
            ).scalar_one_or_none()
        return None if attempts is None else int(attempts)

    async def give_up_orphan(
        self, candidate: dict[str, Any], *, stale_seconds: float, message: str
    ) -> bool:
        """Fail an orphaned job whose attempts are spent (same compare-and-set)."""
        job_id, tenant_id = str(candidate["id"]), str(candidate["tenant_id"])
        if self._db is None:
            job = self._jobs.get(job_id)
            if job is None or not self._mem_still_orphaned(job, candidate, stale_seconds):
                return False
            self._jobs[job_id] = dataclasses.replace(
                job,
                status="failed",
                completed_at=datetime.now(UTC).isoformat(),
                docs_failed=max(job.docs_failed, 1),
                error_message=message[:2048],
            )
            return True
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            failed = (
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                           SET status = 'failed',
                               completed_at = NOW(),
                               docs_failed = GREATEST(docs_failed, 1),
                               error_message = :message
                         WHERE id = :job_id AND tenant_id = :tid
                           AND status IN ('pending', 'running')
                           AND lease_token = :lease_token AND attempts = :attempts
                           AND heartbeat_at < NOW() - make_interval(secs => :stale)
                     RETURNING id
                    """),
                    {
                        "job_id": job_id,
                        "tid": tenant_id,
                        "message": message[:2048],
                        "lease_token": str(candidate["lease_token"]),
                        "attempts": int(candidate["attempts"]),
                        "stale": float(stale_seconds),
                    },
                )
            ).scalar_one_or_none()
        return failed is not None

    async def abandon_requeued_job(
        self, job_id: str, tenant_id: str, *, status: str, message: str
    ) -> bool:
        """Close a requeued job its run will not execute (Source disabled / parked).

        Only a row still waiting for that run (``pending`` under the bare job id)
        is closed; anything a run already claimed is left to it.
        """
        if self._db is None:
            job = self._jobs.get(job_id)
            if job is None or job.status != "pending" or job.lease_token != job_id:
                return False
            self._jobs[job_id] = dataclasses.replace(
                job,
                status=status,
                completed_at=datetime.now(UTC).isoformat(),
                error_message=message[:2048],
            )
            return True
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            closed = (
                await session.execute(
                    text(
                        "UPDATE ingestion_jobs SET status = :status, completed_at = NOW(), "
                        "error_message = :message "
                        "WHERE id = :id AND tenant_id = :tid AND status = 'pending' "
                        "AND lease_token = :id RETURNING id"
                    ),
                    {"status": status, "message": message[:2048], "id": job_id, "tid": tenant_id},
                )
            ).scalar_one_or_none()
        return closed is not None

    @staticmethod
    def _mem_still_orphaned(
        job: IngestionJob, candidate: dict[str, Any], stale_seconds: float
    ) -> bool:
        if job.status not in ACTIVE_JOB_STATUSES:
            return False
        if job.lease_token != candidate["lease_token"] or job.attempts != candidate["attempts"]:
            return False
        beat = job.heartbeat_at or job.started_at or job.created_at
        if not beat:
            return True
        return datetime.now(UTC).timestamp() - datetime.fromisoformat(beat).timestamp() >= (
            stale_seconds
        )

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
    ) -> bool:
        """Add a failed document to the DLQ; True only when the entry was written.

        (DEF-4: a broker-offset connector commits a failed message only once its
        DLQ entry is durable, so the caller needs to know.)

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
            return False
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
            return False
        return True

    async def dead_letter_sourceless(
        self,
        *,
        tenant_id: str,
        doc_id: str,
        error: str,
        payload: dict[str, Any],
        failed_stage: str = "pipeline",
        failure_type: str = "pipeline_failure",
        job_id: str | None = None,
    ) -> str | None:
        """Dead-letter a failure that belongs to no Source; the entry id, or None.

        Direct ingest paths (a single-URL ingest, a repository ingest) have no
        ``source_configs`` row, so their entries carry ``source_id`` NULL (the
        column's FK to ``source_configs`` refused every invented id) and a
        ``payload`` with a ``kind`` the retry job knows how to replay.
        Idempotent: one OPEN entry per (tenant, doc_id) — the partial unique
        index ``ux_ingestion_dlq_open_sourceless`` — so the same failure posted
        again refreshes that entry's error/payload instead of adding a row (its
        retry count and backoff are kept).
        """
        if self._db is None:
            return None
        from sqlalchemy import text

        try:
            payload_json = json.dumps(payload, default=_json_default)
        except (TypeError, ValueError):
            payload_json = json.dumps({"doc_id": doc_id})
        new_id = str(uuid.uuid4())
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text("""
                            INSERT INTO ingestion_dlq
                                (id, dlq_id, source_id, tenant_id, job_id, doc_id,
                                 failed_stage, failure_type, error_message,
                                 raw_doc_json, retry_count, created_at)
                            VALUES
                                (:id, :id, NULL, :tenant_id, :job_id, :doc_id,
                                 :failed_stage, :failure_type, :error,
                                 :raw_doc_json, 0, NOW())
                            ON CONFLICT (tenant_id, doc_id)
                                WHERE source_id IS NULL AND resolved_at IS NULL
                                  AND permanent_failure IS NOT TRUE
                            DO UPDATE SET error_message = EXCLUDED.error_message,
                                          last_error = EXCLUDED.error_message,
                                          raw_doc_json = EXCLUDED.raw_doc_json
                            RETURNING id
                        """),
                        {
                            "id": new_id,
                            "tenant_id": tenant_id,
                            "job_id": job_id,
                            "doc_id": doc_id,
                            "failed_stage": (failed_stage or "pipeline")[:32],
                            "failure_type": (failure_type or "pipeline_failure")[:32],
                            "error": error,
                            "raw_doc_json": payload_json,
                        },
                    )
                ).first()
        except Exception as exc:
            _log.warning("dead_letter_sourceless_error doc=%s: %s", doc_id, exc)
            return None
        return str(row[0]) if row is not None else None

    async def resolve_sourceless_dlq(self, *, tenant_id: str, doc_id: str) -> None:
        """Resolve the open source-less entry of ``doc_id`` (it was ingested since)."""
        await self._update_dlq_entry(
            "UPDATE ingestion_dlq SET resolved_at = NOW() "
            "WHERE tenant_id = :tid AND doc_id = :doc_id AND source_id IS NULL "
            "AND resolved_at IS NULL",
            {"tid": tenant_id, "doc_id": doc_id, "id": doc_id},
            tenant_id=tenant_id,
            op="resolve_sourceless_dlq",
        )

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

    async def resolve_dlq_for_documents(
        self, source_id: str, tenant_id: str, doc_ids: list[str]
    ) -> None:
        """Resolve the Source's open DLQ entries for documents indexed since (P1b-4).

        A later sync that indexes a document whose earlier attempt failed (the
        object was repaired upstream, access was restored) left the old entry
        open: the DLQ kept reporting the failure, and the automatic retry would
        replay the stale bytes over the newer version.
        """
        if not doc_ids:
            return
        await self._update_dlq_entry(
            "UPDATE ingestion_dlq SET resolved_at = NOW() "
            "WHERE tenant_id = :tid AND source_id = :sid AND resolved_at IS NULL "
            "AND doc_id = ANY(:ids)",
            {"tid": tenant_id, "sid": source_id, "ids": list(doc_ids)},
            tenant_id=tenant_id,
            op="resolve_dlq_for_documents",
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
