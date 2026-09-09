"""IngestionScheduler — Celery beat task scheduling for all source sync modes.

Implements:
  - Periodic polling (full + incremental) via Celery beat schedule
  - Event-driven sync via Redis pub/sub (webhook → immediate trigger)
  - Jitter to avoid thundering herd across tenants
  - Distributed locking (LAW-14) — only one sync per source at a time
  - Exponential backoff on repeated failures (LAW-09)
  - Dead-letter queue (DLQ) on permanent failure

LAW-14: Cursor atomicity — only committed after pipeline confirms indexing.
LAW-17: No silent data loss — all failures land in DLQ.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING

from celery import shared_task  # type: ignore[import-not-found]

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

# Maximum jitter (seconds) to spread sync starts across tenants
_MAX_JITTER_SECONDS = 30
# Backoff base (seconds) — doubles on each consecutive failure
_BACKOFF_BASE = 60
# Maximum backoff (10 minutes)
_BACKOFF_MAX = 600


def _jitter(source_id: str) -> float:
    """Deterministic per-source jitter so re-deploys don't reset it."""
    h = int(hashlib.md5(source_id.encode(), usedforsecurity=False).hexdigest()[:8], 16)
    return (h % (_MAX_JITTER_SECONDS * 100)) / 100.0


def _backoff_seconds(consecutive_failures: int) -> float:
    """Exponential backoff capped at _BACKOFF_MAX."""
    raw = _BACKOFF_BASE * (2 ** min(consecutive_failures - 1, 8))
    jitter = random.uniform(0, raw * 0.1)
    return min(raw + jitter, _BACKOFF_MAX)


@shared_task(name="ingestion.sync_source", bind=True, max_retries=5, default_retry_delay=60)
def sync_source_task(
    self, *, source_id: str, tenant_id: str, triggered_by: str = "scheduler"
) -> dict:
    """Celery task: synchronise a single source through the full 13-stage pipeline.

    Called by:
      - Celery beat (periodic polling)
      - Webhook handler (event-driven via on_webhook)
      - Manual trigger (API POST /sources/{id}/sync)
    """
    return asyncio.get_event_loop().run_until_complete(
        _sync_source_async(
            task=self,
            source_id=source_id,
            tenant_id=tenant_id,
            triggered_by=triggered_by,
        )
    )


async def _sync_source_async(*, task, source_id: str, tenant_id: str, triggered_by: str) -> dict:
    """Async body of sync_source_task."""
    from app.ingestion.connector_registry import get_connector
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.pipeline import IngestionPipeline

    tracker = IngestionJobTracker()
    pipeline = IngestionPipeline()

    # ── Distributed lock (LAW-14) ────────────────────────────────────────────
    lock_acquired = await tracker.acquire_lock(source_id, tenant_id, ttl_seconds=3600)
    if not lock_acquired:
        _log.info("source=%s already locked — skipping duplicate sync", source_id)
        return {"skipped": True, "reason": "already_running"}

    # ── Load SourceConfig ────────────────────────────────────────────────────
    config = await tracker.load_config(source_id, tenant_id)
    if config is None:
        await tracker.release_lock(source_id, tenant_id)
        return {"error": "source_not_found"}

    if not config.enabled:
        await tracker.release_lock(source_id, tenant_id)
        return {"skipped": True, "reason": "source_disabled"}

    # ── Backoff check (LAW-09) ───────────────────────────────────────────────
    if config.consecutive_failures and config.consecutive_failures > 0:
        backoff = _backoff_seconds(config.consecutive_failures)
        import time

        if config.last_synced_at:
            import datetime

            last = datetime.datetime.fromisoformat(config.last_synced_at.replace("Z", "+00:00"))
            elapsed = time.time() - last.timestamp()
            if elapsed < backoff:
                await tracker.release_lock(source_id, tenant_id)
                _log.info(
                    "source=%s in backoff (failures=%d, wait=%.0fs, elapsed=%.0fs)",
                    source_id,
                    config.consecutive_failures,
                    backoff,
                    elapsed,
                )
                return {"skipped": True, "reason": "backoff", "retry_in_seconds": backoff - elapsed}

    # ── Get connector ────────────────────────────────────────────────────────
    connector_cls = get_connector(config.source_type)
    if connector_cls is None:
        await tracker.release_lock(source_id, tenant_id)
        return {"error": f"no_connector_for_{config.source_type}"}

    connector = connector_cls()

    # ── Create job record ────────────────────────────────────────────────────
    import uuid as _uuid

    job = await tracker.create_job(
        config,
        job_id=str(_uuid.uuid4()),
        triggered_by=triggered_by,
    )

    docs_indexed = docs_failed = docs_skipped = 0

    try:
        # ── Delta loop ───────────────────────────────────────────────────────
        cursor = config.cursor_value or None
        new_cursor = cursor

        async for raw_doc, next_cursor in connector.get_delta(config, cursor):  # type: ignore[misc]
            try:
                from app.core.config import get_settings
                from app.tenancy.context import PlanTier, TenantContext

                _settings = get_settings()
                tenant_ctx = TenantContext(
                    tenant_id=tenant_id,
                    api_key_id="scheduler",
                    plan=PlanTier.FREE,
                )

                result = await pipeline.ingest(raw_doc, config)

                if result.success:
                    docs_indexed += 1
                elif result.skipped:
                    docs_skipped += 1
                else:
                    docs_failed += 1
                    _log.warning(
                        "pipeline failed: source=%s doc=%s error=%s",
                        source_id,
                        raw_doc.doc_id,
                        result.error,
                    )
                    # DLQ (LAW-17)
                    await tracker.add_to_dlq(
                        source_id=source_id,
                        tenant_id=tenant_id,
                        doc_id=raw_doc.doc_id,
                        error=result.error or "pipeline_failure",
                        raw_doc=raw_doc,
                    )

                new_cursor = next_cursor

                # Commit cursor every 100 docs (LAW-14 atomicity)
                if (docs_indexed + docs_skipped + docs_failed) % 100 == 0:
                    await tracker.update_cursor(job, new_cursor or "", config)

            except Exception as doc_exc:
                docs_failed += 1
                _log.exception(
                    "unhandled error processing doc in source=%s: %s", source_id, doc_exc
                )

        # ── Final cursor commit ───────────────────────────────────────────────
        await tracker.update_cursor(job, new_cursor or "", config)
        # Sync the loop's tallies onto the job before completing it — complete_job
        # records the job's own counters. This path previously called an API that
        # does not exist (job_id=/docs_*/status= kwargs), raising TypeError on
        # every run.
        job.docs_indexed = docs_indexed
        job.docs_skipped = docs_skipped
        job.docs_failed = docs_failed
        await tracker.complete_job(job)
        # Reset failure counter on success
        await tracker.reset_failure_counter(source_id, tenant_id)

        return {
            "job_id": job.job_id,
            "docs_indexed": docs_indexed,
            "docs_skipped": docs_skipped,
            "docs_failed": docs_failed,
        }

    except Exception as exc:
        _log.exception("sync failed for source=%s: %s", source_id, exc)
        job.docs_indexed = docs_indexed
        job.docs_skipped = docs_skipped
        job.docs_failed = docs_failed
        await tracker.complete_job(job, error=str(exc))
        await tracker.increment_failure_counter(source_id, tenant_id)
        # Celery retry
        raise task.retry(exc=exc, countdown=int(_backoff_seconds(1))) from exc

    finally:
        await tracker.release_lock(source_id, tenant_id)


@shared_task(name="ingestion.dispatch_due_sources", bind=True)
def dispatch_due_sources_task(self) -> dict:
    """Celery beat entry point — find all sources due for sync and enqueue them.

    Runs every minute via beat schedule. Each source gets an individual
    sync_source_task with per-source jitter.
    """
    return asyncio.get_event_loop().run_until_complete(_dispatch_due_sources_async())


async def _dispatch_due_sources_async() -> dict:
    """Find sources whose next_sync_at <= now and enqueue sync tasks."""
    import datetime

    from app.ingestion.job_tracker import IngestionJobTracker

    tracker = IngestionJobTracker()
    due_sources = await tracker.get_due_sources()

    dispatched = 0
    for source_id, tenant_id in due_sources:
        jitter = _jitter(source_id)
        sync_source_task.apply_async(
            kwargs={"source_id": source_id, "tenant_id": tenant_id, "triggered_by": "scheduler"},
            countdown=jitter,
        )
        dispatched += 1

    _log.info("dispatch_due_sources: dispatched=%d sources", dispatched)
    return {"dispatched": dispatched, "at": datetime.datetime.utcnow().isoformat()}


@shared_task(name="ingestion.retry_dlq_entries", bind=True)
def retry_dlq_entries_task(self) -> dict:
    """Retry eligible DLQ entries (exponential backoff, max 5 attempts)."""
    return asyncio.get_event_loop().run_until_complete(_retry_dlq_async())


async def _retry_dlq_async() -> dict:
    """Pull eligible DLQ entries and resubmit through the pipeline."""
    from app.core.config import settings
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.pipeline import IngestionPipeline
    from app.tenancy.context import PlanTier, TenantContext

    tracker = IngestionJobTracker()
    pipeline = IngestionPipeline()

    entries = await tracker.get_retryable_dlq_entries(max_entries=50)
    retried = succeeded = still_failed = 0

    for entry in entries:
        if entry.retry_count >= 5:
            await tracker.mark_dlq_permanent_failure(entry.dlq_id)
            continue

        retried += 1
        try:
            tenant_ctx = TenantContext(
                tenant_id=entry.tenant_id,
                api_key_id="dlq_retry",
                plan=getattr(settings, "DEFAULT_PLAN", PlanTier.FREE),
            )
            result = await pipeline.run(
                entry.raw_doc,
                tenant_context=tenant_ctx,
                source_config=getattr(entry, "source_config", None),
            )
            if result.success:
                await tracker.resolve_dlq_entry(entry.dlq_id)
                succeeded += 1
            else:
                await tracker.increment_dlq_retry(entry.dlq_id, error=result.error)
                still_failed += 1
        except Exception as exc:
            await tracker.increment_dlq_retry(entry.dlq_id, error=str(exc))
            still_failed += 1

    _log.info(
        "retry_dlq: retried=%d succeeded=%d still_failed=%d", retried, succeeded, still_failed
    )
    return {"retried": retried, "succeeded": succeeded, "still_failed": still_failed}


# ── Celery beat schedule registration ────────────────────────────────────────
# Called from app/scaling/celery_app.py during setup.
BEAT_SCHEDULE: dict = {
    "ingestion-dispatch-due-sources": {
        "task": "ingestion.dispatch_due_sources",
        "schedule": 60.0,  # every minute
        "options": {"queue": "ingestion"},
    },
    "ingestion-retry-dlq": {
        "task": "ingestion.retry_dlq_entries",
        "schedule": 300.0,  # every 5 minutes
        "options": {"queue": "ingestion"},
    },
}
