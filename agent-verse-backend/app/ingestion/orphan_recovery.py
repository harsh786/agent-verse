"""Worker-loss recovery for knowledge-Source syncs (SYNC-ORPHAN).

The problem
-----------
A sync runs as the ``ingestion.sync_source`` Celery task. When its worker dies
(OOM kill, SIGKILL, pod restart, lost node) nothing finishes the job:

* its ``ingestion_jobs`` row stayed ``running`` until the age-based stale-job
  reaper failed it -- ``ingestion_stale_job_seconds`` (2 h) after it STARTED,
  checked every 10 min -- and it was never run again;
* Celery's own redelivery did not reliably bring it back: a message whose worker
  main process died waits out the ~25 h visibility timeout unless the
  dead-worker restorer knew its owner, and a redelivered *scheduled* sync (a
  dead prefork child's message is requeued at once, ``task_reject_on_worker_lost``)
  took a NEW lock token, found the dead run's lock still alive for up to one
  lock TTL, answered "already running" and was acked -- dropped.

The design
----------
* **Liveness.** A running sync holds the Source's Redis lock under its own
  attempt token (``<job id>#<nonce>``, see ``IngestionJobTracker.hold``) and
  renews it every third of ``ingestion_sync_lock_ttl_seconds``; after every
  renewal it also heart-beats its job row (``heartbeat_at``, compare-and-set on
  the row's ``lease_token``). A job is judged dead only when BOTH signals agree:
  its heartbeat is older than ``ingestion_sync_heartbeat_stale_seconds`` AND the
  Source's lock no longer carries the job's lease token (it expired -- nobody
  renewed it for a whole TTL). A slow run that keeps renewing is never touched,
  however long it runs; a Redis blip that loses the key does not orphan a run
  whose DB heartbeat is fresh, and a DB blip does not orphan a run whose lock is
  still renewed.
* **Requeue, fenced.** :func:`recover_orphaned_syncs` (beat task
  ``ingestion.recover_orphaned_syncs``, every 60 s on the ``maintenance`` queue,
  which keeps running when the ingestion workers are the ones that died) takes
  the free lock under the bare job id (``SET NX``), then requeues the row with a
  compare-and-set on the lease token, attempt count and stale heartbeat it read
  (``status = 'pending'``, ``attempts + 1``, ``requeue_reason``), and queues
  the same job again with an exponential backoff countdown. The requeued run
  claims the lock (rotating it to its own attempt token, so a duplicate
  delivery of either message stands down), bumps the Source's fencing token and
  resumes from the committed cursor with the job's checkpointed counters.
  Documents re-read after the checkpoint are idempotent: unchanged content is
  skipped by the content-hash dedup, edits replace their document atomically.
* **Never double-run.** Each step is a compare-and-set: the lock is taken only
  while free, the row is requeued only while it still carries the dead run's
  token and stale heartbeat, and a run that was in fact alive finds its row
  gone at its next heartbeat or checkpoint (its lease is lost, its cursor and
  result writes match nothing) and stops.
* **Bounded.** After ``ingestion_sync_max_attempts`` runs the job is failed
  with the reason (visible on ``GET /sources/{id}/sync/history``: ``attempts``,
  ``requeue_reason``, ``error_message``) and the Source's failure counter is
  bumped, so the next scheduled sync is backed off and resumes from the last
  checkpoint. A Source whose re-dispatched scheduled sync supersedes a dead job
  continues that job's attempt count (``plan_job_claim``), so a sync that kills
  its worker every time stays bounded too.

Recovery time: a dead run is requeued within ``max(lock TTL, heartbeat stale
window) + 60 s`` (≈ 6 min at the defaults) of its last heartbeat, instead of
the 2 h + 10 min it took to be failed (and never retried) before.
"""

from __future__ import annotations

import contextlib
import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.job_tracker import exhausted_message
from app.observability.logging import get_logger

_log = get_logger(__name__)

# A requeued job's lock waits this long for its task to start (the API's
# queued-sync TTL). A message lost before it starts is restored by the
# dead-worker restorer; this TTL is the backstop for anything else.
QUEUED_LOCK_TTL_SECONDS = 3600


@dataclass(frozen=True)
class RecoverySettings:
    enabled: bool = True
    stale_seconds: float = 300.0
    max_attempts: int = 3
    backoff_seconds: int = 30
    backoff_max_seconds: int = 600
    scan_limit: int = 200


def recovery_settings() -> RecoverySettings:
    from app.core.config import get_settings

    s = get_settings()
    return RecoverySettings(
        enabled=bool(s.ingestion_sync_orphan_recovery_enabled),
        stale_seconds=float(s.ingestion_sync_heartbeat_stale_seconds),
        max_attempts=int(s.ingestion_sync_max_attempts),
        backoff_seconds=int(s.ingestion_sync_requeue_backoff_seconds),
        backoff_max_seconds=int(s.ingestion_sync_requeue_backoff_max_seconds),
    )


class Decision(enum.StrEnum):
    ALIVE = "alive"  # the lock still carries the job's lease token
    BUSY = "busy"  # another run holds the Source's lock
    REQUEUE = "requeue"
    GIVE_UP = "give_up"


def classify_orphan(
    candidate: dict[str, Any], holder: str | None, *, max_attempts: int
) -> Decision:
    """What to do with an active job whose heartbeat went stale.

    ``holder`` is the Source's lock value right now. Equal to the job's lease
    token: its run still holds the lock (renewing it, or a requeued job waiting
    in the queue) -- alive, whatever the heartbeat says. Another value: some
    other run holds the Source; a sync that took it supersedes this job when it
    claims, a reconcile / migration releases it soon -- look again next pass.
    Free: the run is dead. Requeue it, unless its attempts are spent.
    """
    if holder is not None and holder == str(candidate.get("lease_token") or ""):
        return Decision.ALIVE
    if holder is not None:
        return Decision.BUSY
    if int(candidate.get("attempts") or 1) >= max_attempts:
        return Decision.GIVE_UP
    return Decision.REQUEUE


def requeue_countdown(attempt: int, *, base_seconds: int, max_seconds: int) -> int:
    """Backoff before requeued attempt ``attempt`` (2, 3, ...): base, 2x, 4x ... capped."""
    if base_seconds <= 0:
        return 0
    return int(min(base_seconds * (2 ** max(0, attempt - 2)), max(max_seconds, base_seconds)))


def resume_as_reindex(candidate: dict[str, Any]) -> bool:
    """A reindex that died before its first checkpoint redoes the delete + full sync.

    The delete precedes every cursor commit, so a committed cursor proves it
    finished: the run resumes as an ordinary sync from there. Without one the
    delete may be partial -- running it again is exact (and idempotent).
    """
    return str(candidate.get("triggered_by") or "") == "reindex" and not str(
        candidate.get("cursor_after") or ""
    )


def orphan_reason(candidate: dict[str, Any]) -> str:
    age = int(float(candidate.get("heartbeat_age_s") or 0.0))
    return (
        f"worker lost: no heartbeat for {age}s and its sync lock expired; requeued to "
        "resume from the last checkpoint"
    )


@dataclass
class RecoveryReport:
    scanned: int = 0
    alive: int = 0
    busy: int = 0
    requeued: list[dict[str, Any]] = field(default_factory=list)
    gave_up: list[str] = field(default_factory=list)
    lost_race: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "alive": self.alive,
            "busy": self.busy,
            "requeued": len(self.requeued),
            "requeued_jobs": self.requeued,
            "gave_up": len(self.gave_up),
            "gave_up_jobs": self.gave_up,
            "lost_race": self.lost_race,
            "errors": self.errors,
        }


Enqueue = Callable[[dict[str, Any], int], None]


def _default_enqueue(kwargs: dict[str, Any], countdown: int) -> None:
    from app.ingestion.scheduler import sync_source_task

    sync_source_task.apply_async(kwargs=kwargs, countdown=countdown, queue="ingestion")


async def recover_orphaned_syncs(
    tracker: Any,
    *,
    source_store: Any,
    settings: RecoverySettings,
    enqueue: Enqueue | None = None,
) -> RecoveryReport:
    """Requeue (or give up on) every sync job whose worker died. Idempotent.

    Safe to run concurrently with itself, with live syncs and with Celery's own
    redelivery: every transition is a compare-and-set (see the module docstring).
    """
    send = enqueue or _default_enqueue
    report = RecoveryReport()
    candidates = await tracker.find_orphan_candidates(
        stale_seconds=settings.stale_seconds, limit=settings.scan_limit
    )
    report.scanned = len(candidates)
    for cand in candidates:
        job_id, tenant_id, source_id = (
            str(cand["id"]), str(cand["tenant_id"]), str(cand["source_id"])
        )
        try:
            holder = await tracker.lock_holder(source_id, tenant_id)
            decision = classify_orphan(cand, holder, max_attempts=settings.max_attempts)
            if decision is Decision.ALIVE:
                report.alive += 1
            elif decision is Decision.BUSY:
                report.busy += 1
            elif decision is Decision.GIVE_UP:
                await _give_up(tracker, source_store, cand, settings, report)
            else:
                await _requeue(tracker, cand, settings, send, report)
        except Exception as exc:
            report.errors += 1
            _log.warning(
                "ingestion_orphan_recovery_failed",
                job_id=job_id, source_id=source_id,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
    if report.requeued or report.gave_up or report.errors:
        _log.warning("ingestion_orphan_recovery", **report.as_dict())
    return report


async def _give_up(
    tracker: Any,
    source_store: Any,
    cand: dict[str, Any],
    settings: RecoverySettings,
    report: RecoveryReport,
) -> None:
    job_id, tenant_id, source_id = str(cand["id"]), str(cand["tenant_id"]), str(cand["source_id"])
    message = exhausted_message(int(cand["attempts"]), orphan_reason(cand))
    if not await tracker.give_up_orphan(
        cand, stale_seconds=settings.stale_seconds, message=message
    ):
        report.lost_race += 1
        return
    report.gave_up.append(job_id)
    _log.error(
        "ingestion_sync_gave_up",
        job_id=job_id, tenant_id=tenant_id, source_id=source_id, attempts=cand["attempts"],
    )
    # The Source's failure counter backs off its next scheduled sync, which
    # resumes from the last committed cursor.
    with contextlib.suppress(Exception):
        await source_store.mark_synced(source_id, tenant_id, docs_indexed=0, chunks=0, failed=1)


async def _requeue(
    tracker: Any,
    cand: dict[str, Any],
    settings: RecoverySettings,
    send: Enqueue,
    report: RecoveryReport,
) -> None:
    job_id, tenant_id, source_id = str(cand["id"]), str(cand["tenant_id"]), str(cand["source_id"])
    # 1. The free lock, under the bare job id: only one recoverer (or one new
    #    sync) can take it; a run that is somehow still alive cannot renew it.
    if not await tracker.queue_lock(
        source_id, tenant_id, job_id, ttl_seconds=QUEUED_LOCK_TTL_SECONDS
    ):
        report.lost_race += 1
        return
    # 2. The row, compare-and-set on what the scan read.
    attempt = await tracker.requeue_orphan(
        cand, stale_seconds=settings.stale_seconds, reason=orphan_reason(cand)
    )
    if attempt is None:
        await tracker.release_lock(source_id, tenant_id, job_id)
        report.lost_race += 1
        return
    countdown = requeue_countdown(
        attempt, base_seconds=settings.backoff_seconds, max_seconds=settings.backoff_max_seconds
    )
    kwargs: dict[str, Any] = {
        "source_id": source_id,
        "tenant_id": tenant_id,
        "triggered_by": str(cand.get("triggered_by") or "scheduler"),
        "job_id": job_id,
        "reindex": resume_as_reindex(cand),
        "resume": True,
    }
    # 3. The task. If the broker refuses it, free the lock: the row keeps a
    #    fresh heartbeat and is requeued again once that goes stale.
    try:
        send(kwargs, countdown)
    except Exception:
        await tracker.release_lock(source_id, tenant_id, job_id)
        raise
    report.requeued.append({"job_id": job_id, "attempt": attempt, "countdown_s": countdown})
    _log.warning(
        "ingestion_sync_requeued",
        job_id=job_id, tenant_id=tenant_id, source_id=source_id,
        attempt=attempt, countdown_s=countdown,
        heartbeat_age_s=round(float(cand.get("heartbeat_age_s") or 0.0), 1),
    )


__all__ = [
    "QUEUED_LOCK_TTL_SECONDS",
    "Decision",
    "RecoveryReport",
    "RecoverySettings",
    "classify_orphan",
    "orphan_reason",
    "recover_orphaned_syncs",
    "recovery_settings",
    "requeue_countdown",
    "resume_as_reindex",
]
