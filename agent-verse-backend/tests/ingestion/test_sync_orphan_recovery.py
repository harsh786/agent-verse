"""SYNC-ORPHAN unit tests: detection, fencing, attempt bounds and backoff.

The real Postgres + Redis behaviour (a worker dies mid-sync, the job resumes
exactly; a slow live worker is left alone) is in
``test_sync_orphan_recovery_integration.py``. Here the policy and the in-memory
tracker paths are pinned without infrastructure.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.job_tracker import (
    REDELIVERED_REASON,
    IngestionJobTracker,
    SyncLockLostError,
    lock_job_id,
    plan_job_claim,
)
from app.ingestion.orphan_recovery import (
    Decision,
    RecoverySettings,
    classify_orphan,
    recover_orphaned_syncs,
    requeue_countdown,
    resume_as_reindex,
)
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig
from app.ingestion.source_store import SourceConfigStore


def _config(**overrides: Any) -> SourceConfig:
    base: dict[str, Any] = {
        "source_id": "src-1",
        "tenant_id": "t1",
        "name": "kb",
        "family": "web",
        "source_type": "http",
        "collection_id": "col-1",
    }
    base.update(overrides)
    return SourceConfig(**base)


def _settings(**overrides: Any) -> RecoverySettings:
    base: dict[str, Any] = {"stale_seconds": 60.0, "max_attempts": 3, "backoff_seconds": 30}
    base.update(overrides)
    return RecoverySettings(**base)


def _age(tracker: IngestionJobTracker, job_id: str, seconds: float) -> None:
    """Pretend the job's last heartbeat was ``seconds`` ago."""
    job = tracker._jobs[job_id]
    job.heartbeat_at = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


async def _running_job(
    tracker: IngestionJobTracker, config: SourceConfig, job_id: str = "job-1"
) -> tuple[Any, Any]:
    lease = await tracker.hold(config.source_id, config.tenant_id, job_id, ttl_seconds=60)
    assert lease is not None
    job = await tracker.create_job(
        config, job_id=job_id, triggered_by="scheduler", lease_token=lease.token, max_attempts=3
    )
    return lease, job


async def _die(tracker: IngestionJobTracker, lease: Any, config: SourceConfig) -> None:
    """The worker is gone: nothing renews the lock, which then expires."""
    if lease._task is not None:
        lease._task.cancel()
    tracker._locks.pop(config.source_id, None)


# ── Detection policy ──────────────────────────────────────────────────────────


def _cand(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "job-1", "tenant_id": "t1", "source_id": "src-1", "attempts": 1,
        "lease_token": "job-1#abc", "triggered_by": "scheduler", "cursor_after": "",
        "heartbeat_age_s": 400.0,
    }
    base.update(overrides)
    return base


def test_a_job_whose_lock_still_carries_its_token_is_alive_whatever_its_heartbeat() -> None:
    assert classify_orphan(_cand(), "job-1#abc", max_attempts=3) is Decision.ALIVE
    # A requeued job waiting in the queue holds the lock under its bare id.
    assert classify_orphan(_cand(lease_token="job-1"), "job-1", max_attempts=3) is Decision.ALIVE


def test_another_holder_means_look_again_later() -> None:
    assert classify_orphan(_cand(), "other-job#x", max_attempts=3) is Decision.BUSY


def test_a_free_lock_and_a_stale_heartbeat_is_a_dead_run() -> None:
    assert classify_orphan(_cand(attempts=1), None, max_attempts=3) is Decision.REQUEUE
    assert classify_orphan(_cand(attempts=2), None, max_attempts=3) is Decision.REQUEUE
    assert classify_orphan(_cand(attempts=3), None, max_attempts=3) is Decision.GIVE_UP


def test_requeue_backoff_is_exponential_and_capped() -> None:
    assert [requeue_countdown(a, base_seconds=30, max_seconds=600) for a in (2, 3, 4, 5, 6, 9)] == [
        30, 60, 120, 240, 480, 600
    ]
    assert requeue_countdown(2, base_seconds=0, max_seconds=600) == 0


def test_a_reindex_resumes_as_a_plain_sync_once_it_checkpointed() -> None:
    assert resume_as_reindex(_cand(triggered_by="reindex", cursor_after="")) is True
    assert resume_as_reindex(_cand(triggered_by="reindex", cursor_after="42")) is False
    assert resume_as_reindex(_cand(triggered_by="scheduler", cursor_after="")) is False


# ── Claim plan (fencing + bounded attempts) ───────────────────────────────────


def test_claim_plan_first_run_and_finished_job() -> None:
    first = plan_job_claim(
        existing=None, others=[], job_id="j", max_attempts=3, inherit_attempts=True
    )
    assert (first.action, first.attempts, first.reason, first.exhausted) == ("insert", 1, "", False)
    done = plan_job_claim(
        existing={"status": "completed", "attempts": 1, "lease_token": "j#1"},
        others=[], job_id="j", max_attempts=3, inherit_attempts=True,
    )
    assert done.action == "finished"


def test_claim_plan_counts_a_broker_redelivery_but_not_a_recovery_requeue() -> None:
    requeued = plan_job_claim(
        existing={"status": "pending", "attempts": 2, "lease_token": "j", "requeue_reason": "r"},
        others=[], job_id="j", max_attempts=3, inherit_attempts=True,
    )
    assert (requeued.action, requeued.attempts, requeued.reason) == ("resume", 2, "r")
    redelivered = plan_job_claim(
        existing={"status": "running", "attempts": 1, "lease_token": "j#dead"},
        others=[], job_id="j", max_attempts=3, inherit_attempts=True,
    )
    assert (redelivered.attempts, redelivered.reason) == (2, REDELIVERED_REASON)


def test_claim_plan_a_scheduled_rerun_continues_a_dead_jobs_attempts() -> None:
    plan = plan_job_claim(
        existing=None, others=[{"id": "old", "attempts": 2}], job_id="new",
        max_attempts=3, inherit_attempts=True,
    )
    assert plan.superseded == ("old",)
    assert plan.attempts == 3 and "old" in plan.reason and not plan.exhausted
    operator = plan_job_claim(
        existing=None, others=[{"id": "old", "attempts": 2}], job_id="new",
        max_attempts=3, inherit_attempts=False,
    )
    assert operator.superseded == ("old",) and operator.attempts == 1


def test_claim_plan_past_max_attempts_fails_honestly() -> None:
    plan = plan_job_claim(
        existing={"status": "running", "attempts": 3, "lease_token": "j#dead"},
        others=[], job_id="j", max_attempts=3, inherit_attempts=True,
    )
    assert plan.exhausted and plan.attempts == 3
    assert "gave up after 3 attempt(s)" in plan.error


# ── Lock fencing ──────────────────────────────────────────────────────────────


async def test_a_run_rotates_the_lock_so_a_duplicate_delivery_stands_down() -> None:
    tracker = IngestionJobTracker()
    token = await tracker.acquire_lock("src-1", "t1")
    assert token
    lease = await tracker.hold("src-1", "t1", token, ttl_seconds=60)
    assert lease is not None and lease.token.startswith(f"{token}#")
    assert lease.job_id == token and lock_job_id(lease.token) == token
    # The same task message delivered again while the first run is alive.
    assert await tracker.hold("src-1", "t1", token, ttl_seconds=60) is None
    assert await tracker.running_job_id("src-1", "t1") == token
    await lease.release()
    assert await tracker.running_job_id("src-1", "t1") is None


async def test_cancel_reaches_a_scheduled_run() -> None:
    """The scheduler's lock token is the run's job id, so cancel finds it."""
    from app.ingestion.scheduler import _sync_source_async

    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    started = asyncio.Event()

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> Any:
            for i in range(50):
                if i == 5:
                    started.set()
                    await asyncio.sleep(0.05)
                yield (
                    RawDocument(doc_id=f"d{i}", source_id="src-1", tenant_id="t1",
                                content=b"x", content_type="text/plain"),
                    str(i),
                )

    pipeline = MagicMock()
    pipeline.ingest = AsyncMock(
        side_effect=lambda raw, cfg: PipelineResult(
            doc_id=raw.doc_id, source_id="src-1", tenant_id="t1", status="indexed"
        )
    )
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
    ):
        run = asyncio.ensure_future(_sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="scheduler"))
        await started.wait()
        cancelled_job = await tracker.request_cancel("src-1", "t1")
        result = await run
    assert result["job_id"] == cancelled_job and result["cancelled"] is True


# ── In-memory tracker: requeue, zombie fencing, give-up ───────────────────────


async def test_recovery_requeues_a_dead_run_and_fences_the_zombie() -> None:
    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, job = await _running_job(tracker, config)
    sent: list[tuple[dict[str, Any], int]] = []

    def _send(kwargs: dict[str, Any], countdown: int) -> None:
        sent.append((kwargs, countdown))

    # Alive (lock renewed) with a stale heartbeat: left alone.
    _age(tracker, "job-1", 600)
    report = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                          enqueue=_send)
    assert (report.scanned, report.alive, report.requeued) == (1, 1, [])

    await _die(tracker, lease, config)
    report = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                          enqueue=_send)
    assert [r["job_id"] for r in report.requeued] == ["job-1"]
    (kwargs, countdown), = sent
    assert countdown == 30 and kwargs["job_id"] == "job-1" and kwargs["resume"] is True
    row = tracker._jobs["job-1"]
    assert (row.status, row.attempts, row.lease_token) == ("pending", 2, "job-1")
    assert "worker lost" in row.requeue_reason
    assert tracker._locks["src-1"] == "job-1"  # the queued lock

    # The old run turns out to be alive after all: it is fenced off everywhere.
    assert await tracker.heartbeat_job(job) is False
    with pytest.raises(SyncLockLostError):
        await tracker.update_cursor(job, "zombie", config)
    await tracker.complete_job(job)
    assert tracker._jobs["job-1"].status == "pending"

    # The requeued run claims the job (attempt 2, not counted twice).
    new_lease = await tracker.hold("src-1", "t1", "job-1", ttl_seconds=60)
    assert new_lease is not None
    resumed = await tracker.create_job(config, job_id="job-1", lease_token=new_lease.token,
                                       max_attempts=3)
    assert (resumed.status, resumed.attempts) == ("running", 2)
    # A second recovery pass does nothing to a live, claimed run.
    again = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                         enqueue=_send)
    assert not again.requeued and len(sent) == 1
    await new_lease.release()
    await lease.release()


async def test_recovery_gives_up_after_max_attempts_and_backs_off_the_source() -> None:
    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, _job = await _running_job(tracker, config)
    tracker._jobs["job-1"].attempts = 3
    await _die(tracker, lease, config)
    _age(tracker, "job-1", 600)
    sent: list[Any] = []
    report = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                          enqueue=lambda k, c: sent.append(k))
    assert report.gave_up == ["job-1"] and not sent
    row = tracker._jobs["job-1"]
    assert row.status == "failed" and "gave up after 3 attempt(s)" in row.error_message
    assert row.docs_failed >= 1
    stored = await store.get("src-1", "t1")
    assert stored is not None and stored.consecutive_failures == 1
    assert "src-1" not in tracker._locks
    await lease.release()


async def test_a_young_heartbeat_is_not_a_candidate_and_a_broker_failure_frees_the_lock() -> None:
    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, _job = await _running_job(tracker, config)
    await _die(tracker, lease, config)
    report = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings())
    assert report.scanned == 0  # heartbeat younger than the stale window

    _age(tracker, "job-1", 600)

    def _broker_down(kwargs: dict[str, Any], countdown: int) -> None:
        raise ConnectionError("broker down")

    report = await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                          enqueue=_broker_down)
    assert report.errors == 1 and not report.requeued
    assert "src-1" not in tracker._locks  # freed for the next pass
    assert tracker._jobs["job-1"].status == "pending"  # fresh heartbeat: retried later
    await lease.release()


async def test_a_lost_requeue_race_takes_nothing() -> None:
    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, _job = await _running_job(tracker, config)
    await _die(tracker, lease, config)
    _age(tracker, "job-1", 600)
    stale = await tracker.find_orphan_candidates(stale_seconds=60)
    # Between the scan and the requeue the dead job's message was redelivered
    # and claimed: the compare-and-set on its lease token fails.
    tracker._jobs["job-1"] = dataclasses.replace(tracker._jobs["job-1"], lease_token="job-1#new")
    assert await tracker.requeue_orphan(stale[0], stale_seconds=60, reason="r") is None
    assert await tracker.give_up_orphan(stale[0], stale_seconds=60, message="m") is False
    await lease.release()


async def test_a_scheduled_rerun_supersedes_a_dead_job_and_inherits_its_attempts() -> None:
    tracker = IngestionJobTracker()
    config = _config()
    lease, _job = await _running_job(tracker, config, job_id="old")
    tracker._jobs["old"].attempts = 2
    await _die(tracker, lease, config)
    # The due-scan's next run takes the free lock first.
    token = await tracker.acquire_lock("src-1", "t1")
    assert token
    new_lease = await tracker.hold("src-1", "t1", token, ttl_seconds=60)
    assert new_lease is not None
    job = await tracker.create_job(config, job_id=token, lease_token=new_lease.token,
                                   max_attempts=3)
    assert (job.status, job.attempts) == ("running", 3)
    assert "old" in job.requeue_reason
    old = tracker._jobs["old"]
    assert old.status == "failed" and token in old.error_message
    await new_lease.release()
    await lease.release()


async def test_a_finished_job_is_not_run_again_by_a_late_delivery() -> None:
    from app.ingestion.scheduler import _sync_source_async

    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, job = await _running_job(tracker, config)
    await tracker.complete_job(job)
    await lease.release()
    connector = MagicMock()
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, MagicMock(), store)),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="manual",
            job_id="job-1",
        )
    assert result == {"skipped": True, "reason": "job_already_finished", "job_id": "job-1"}
    connector.return_value.get_delta.assert_not_called()
    assert tracker._jobs["job-1"].status == "completed"


async def test_a_redelivery_past_max_attempts_fails_the_job_and_backs_off() -> None:
    from app.ingestion.scheduler import _sync_source_async

    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, _job = await _running_job(tracker, config)
    tracker._jobs["job-1"].attempts = 3
    await _die(tracker, lease, config)
    # The dead child's message comes straight back (task_reject_on_worker_lost).
    with patch("app.ingestion.scheduler._build_worker_ingestion",
               return_value=(tracker, MagicMock(), store)):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by="manual",
            job_id="job-1",
        )
    assert result["error"] == "attempts_exhausted" and "gave up" in result["detail"]
    row = tracker._jobs["job-1"]
    assert row.status == "failed" and row.attempts == 3
    stored = await store.get("src-1", "t1")
    assert stored is not None and stored.consecutive_failures == 1
    assert "src-1" not in tracker._locks  # released by the run
    await lease.release()


async def test_a_requeued_job_of_a_disabled_source_is_closed() -> None:
    from app.ingestion.scheduler import _sync_source_async

    tracker = IngestionJobTracker()
    store = SourceConfigStore()
    config = _config()
    await store.create(config)
    lease, _job = await _running_job(tracker, config)
    await _die(tracker, lease, config)
    _age(tracker, "job-1", 600)
    sent: list[dict[str, Any]] = []
    await recover_orphaned_syncs(tracker, source_store=store, settings=_settings(),
                                 enqueue=lambda k, c: sent.append(k))
    await store.update("src-1", "t1", enabled=False)
    with patch("app.ingestion.scheduler._build_worker_ingestion",
               return_value=(tracker, MagicMock(), store)):
        result = await _sync_source_async(task=MagicMock(), **sent[0])
    assert result["reason"] == "source_disabled"
    assert tracker._jobs["job-1"].status == "cancelled"
    await lease.release()


async def test_the_heartbeat_loses_the_lease_when_the_job_was_taken_over() -> None:
    tracker = IngestionJobTracker()
    config = _config()
    lease, job = await _running_job(tracker, config)
    lease.ttl_seconds = 0.15  # renew (and beat) every 50 ms
    lease._task.cancel()  # type: ignore[union-attr]
    lease.start()
    lease.attach_heartbeat(lambda: tracker.heartbeat_job(job))
    await asyncio.sleep(0.12)
    assert not lease.lost
    before = tracker._jobs["job-1"].heartbeat_at
    await asyncio.sleep(0.12)
    assert tracker._jobs["job-1"].heartbeat_at != before  # beaten on renewal
    # Orphan recovery requeued it (it judged the run dead): the run stops.
    tracker._jobs["job-1"] = dataclasses.replace(tracker._jobs["job-1"], lease_token="job-1")
    for _ in range(40):
        if lease.lost:
            break
        await asyncio.sleep(0.02)
    assert lease.lost and "requeued" in lease.reason
    with pytest.raises(SyncLockLostError):
        lease.check()
    await lease.release()


async def test_the_age_reaper_never_fails_a_heart_beating_leased_run() -> None:
    tracker = IngestionJobTracker()
    config = _config()
    lease, _job = await _running_job(tracker, config)
    long_ago = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    tracker._jobs["job-1"].started_at = long_ago  # a five-hour sync, still beating
    assert await tracker.reap_stale_jobs(older_than_seconds=7200) == []
    tracker._jobs["job-1"].heartbeat_at = long_ago  # silent for five hours: backstop
    reaped = await tracker.reap_stale_jobs(older_than_seconds=7200)
    assert [r["id"] for r in reaped] == ["job-1"]
    await lease.release()


async def test_sync_history_serves_attempts_and_reason_never_the_lease_token() -> None:
    tracker = IngestionJobTracker()
    config = _config()
    lease, _job = await _running_job(tracker, config)
    (item,) = await tracker.list_jobs("src-1", "t1")
    assert item["attempts"] == 1 and item["requeue_reason"] == "" and item["heartbeat_at"]
    assert "lease_token" not in item
    await lease.release()


# ── Wiring ────────────────────────────────────────────────────────────────────


def test_recovery_runs_every_minute_on_the_maintenance_pool() -> None:
    from app.scaling.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["ingestion-recover-orphaned-syncs"]
    assert entry["task"] == "ingestion.recover_orphaned_syncs"
    assert entry["schedule"] == 60.0 and entry["options"]["queue"] == "maintenance"
    assert celery_app.conf.task_routes["ingestion.recover_orphaned_syncs"] == {
        "queue": "maintenance"
    }
    from app.ingestion import scheduler

    assert scheduler.recover_orphaned_syncs_task.name == "ingestion.recover_orphaned_syncs"


def test_settings_defaults_bound_recovery_to_minutes() -> None:
    from app.core.config import Settings
    from app.ingestion.orphan_recovery import recovery_settings

    s = Settings()
    assert s.ingestion_sync_orphan_recovery_enabled is True
    assert s.ingestion_sync_heartbeat_stale_seconds == 300
    assert s.ingestion_sync_max_attempts == 3
    # Detection bound: max(lock TTL, stale window) + the 60 s beat.
    bound = max(s.ingestion_sync_lock_ttl_seconds, s.ingestion_sync_heartbeat_stale_seconds) + 60
    assert bound <= 360
    assert recovery_settings().max_attempts == s.ingestion_sync_max_attempts


async def test_the_beat_task_refuses_without_a_shared_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion import scheduler

    monkeypatch.setattr(scheduler, "_reconcile_redis", lambda: None)
    assert await scheduler._recover_orphaned_syncs_async() == {
        "skipped": True, "reason": "no_shared_lock"
    }
