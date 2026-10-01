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

import hashlib
import logging
import random
from typing import TYPE_CHECKING, Any

from celery import shared_task  # type: ignore[import-not-found]

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


def _run_task_loop(coro: Any) -> Any:
    """Celery entry → async body on a fresh, fully torn-down loop.

    The persistent ``get_event_loop()`` shared the module-level DB engine with
    the scaling tasks' throw-away loops in the same worker process, so pooled
    asyncpg connections crossed loops and leaked "idle in transaction".
    """
    from app.db.session import run_in_fresh_loop

    return run_in_fresh_loop(coro)


# Maximum jitter (seconds) to spread sync starts across tenants
_MAX_JITTER_SECONDS = 30
# Backoff base (seconds) — doubles on each consecutive failure
_BACKOFF_BASE = 60
# Maximum backoff (10 minutes)
_BACKOFF_MAX = 600
# DLQ entries retried this many times are flagged permanent by the retry job.
_DLQ_MAX_RETRIES = 5


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
    self,
    *,
    source_id: str,
    tenant_id: str,
    triggered_by: str = "scheduler",
    job_id: str | None = None,
    reindex: bool = False,
) -> dict:
    """Celery task: synchronise a single source through the full 13-stage pipeline.

    Called by:
      - Celery beat (periodic polling)
      - Webhook handler (event-driven via on_webhook)
      - Manual trigger (API POST /sources/{id}/sync)
      - Reindex (API POST /sources/{id}/reindex, ``reindex=True``): the source's
        indexed documents are deleted and it is re-synced from the start.
    """
    return _run_task_loop(
        _sync_source_async(
            task=self,
            source_id=source_id,
            tenant_id=tenant_id,
            triggered_by=triggered_by,
            job_id=job_id,
            reindex=reindex,
        )
    )


def _build_worker_ingestion() -> tuple[object, object, object]:
    """Build DB-backed (tracker, pipeline, source_store) for the Celery worker.

    The FastAPI lifespan never runs in a worker, so the pipeline must be wired
    with the real KnowledgeStore + embedder here (previously ``IngestionPipeline()``
    with no deps silently skipped embedding — Stage 10 ``no_embedder`` — so no
    scheduled document was ever indexed), and the source store must be DB-backed
    so the config actually loads cross-process.

    Everything here acts for ONE tenant at a time, so it all runs on the
    application factory under that tenant's RLS context. The tracker also gets
    the maintenance-role factory, which only its cross-tenant DLQ scan uses.
    """
    from app.db.session import get_session_factory, get_system_session_factory
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.pii import build_pii_analyzer
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.quota import IngestionQuotaEnforcer
    from app.ingestion.source_store import SourceConfigStore
    from app.providers.embedder_factory import resolve_embedder
    from app.rag.store import KnowledgeStore

    db_factory = get_session_factory()
    # The SAME embedder the API's retrieval embeds queries with — not the chat
    # LLM provider (resolve_provider()), whose vectors live in a different
    # model/space, so scheduled documents were unretrievable by similarity.
    # resolve_embedder applies the NVIDIA / on-prem embedding endpoint itself
    # (create_app never runs in a worker) and logs any load failure loudly.
    resolution = resolve_embedder()
    embedder = resolution.embedder
    knowledge_store = KnowledgeStore(db_factory, embedding_dim=resolution.dimension)
    # Documents indexed here must invalidate answers the API replicas cached
    # from the tenant's old knowledge (shared Redis generation).
    from app.rag.semantic_cache import bump_knowledge_generation

    knowledge_store.add_change_listener(bump_knowledge_generation)
    # Worker embeds count into the tenant's shared usage counters too.
    from app.embedding.usage import configure_usage_redis_from_env

    configure_usage_redis_from_env()
    # Stage 1 (quota) and Stage 6 (PII) were never wired here, so every
    # scheduled/DLQ-retried document skipped both.
    pipeline = IngestionPipeline(
        knowledge_store=knowledge_store,
        embedder=embedder,
        pii_analyzer=build_pii_analyzer(),
        quota_enforcer=IngestionQuotaEnforcer(db_factory),
        kg_hook=_build_worker_kg_hook(db_factory),
    )
    _bind_worker_guardrail_rules(db_factory)
    tracker = IngestionJobTracker(db=db_factory, system_db=get_system_session_factory())
    source_store = SourceConfigStore(db=db_factory)
    return tracker, pipeline, source_store


def _build_worker_kg_hook(db_factory: object) -> object:
    """The D-15 graph auto-population hook, persisted through the worker's factory.

    It was never built here, so documents from scheduled / DLQ-retried syncs
    produced no graph entities (only API-process ingestion did). Same
    configuration as the API's hook: deterministic extraction (no LLM spend),
    writes batched and awaited under each tenant's RLS context.
    """
    from app.knowledge_graph.ingestion_hook import KGIngestionHook
    from app.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore()
    store.set_db(db_factory)
    return KGIngestionHook(store=store)


def _bind_worker_guardrail_rules(db_factory: object) -> None:
    """Bind the RAG_INGEST guardrail engine to the tenant's persisted rules.

    ``app.main``'s lifespan does this for the API; it never runs in a worker, so
    ``ensure_tenant_loaded`` was a no-op here and scheduled / DLQ-retried syncs
    were screened against the baseline defaults only — a tenant's own block
    rules never applied. Each tenant's rules load under that tenant's RLS
    context on its first evaluation. The worker writes no rules, so nothing is
    auto-persisted. A failure here propagates: the sync must not run unscreened
    (and ``screen_text`` refuses to screen in production without a repository).
    """
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.repository import PostgresGuardrailRuleRepository

    if not guardrails_engine.has_repository:
        guardrails_engine.bind_repository(
            PostgresGuardrailRuleRepository(db_factory),  # type: ignore[arg-type]
            auto_persist=False,
        )


def _build_worker_legal_holds() -> object | None:
    """The legal-hold checker for the worker (the API wires its own in lifespan)."""
    from app.db.session import get_session_factory
    from app.governance.legal_holds import LegalHoldManager

    return LegalHoldManager(redis=None, db_factory=get_session_factory())


_RECONCILE_PAGE = 500


async def _reconcile_upstream_deletions(connector: Any, config: Any, pipeline: Any) -> int:
    """Delete this Source's indexed documents that no longer exist upstream.

    Runs only for connectors whose ``list_live_doc_ids`` returns an authoritative
    set (``None`` = cannot know → nothing is deleted). Only documents stamped
    with this Source's id are candidates; a document (or its collection) under
    legal hold is kept, and a hold that cannot be verified keeps it too (fail
    closed). Any listing error skips reconciliation — the sync itself stands.
    """
    try:
        live = await connector.list_live_doc_ids(config)
    except Exception as exc:
        _log.warning(
            "upstream_deletion_listing_failed source=%s: %s", config.source_id, exc
        )
        return 0
    store = getattr(pipeline, "_kb", None)
    if live is None or store is None or not config.collection_id:
        return 0

    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(
        tenant_id=config.tenant_id, plan=PlanTier.FREE, api_key_id="ingestion"
    )
    stale: list[str] = []
    after: str | None = None
    while True:
        rows = await store.list_source_documents_async(
            tenant_ctx=tenant_ctx,
            collection_id=config.collection_id,
            source_id=config.source_id,
            limit=_RECONCILE_PAGE,
            after=after,
        )
        stale.extend(
            str(r["id"])
            for r in rows
            if str(r["id"]) not in live and connector.manages_doc_id(str(r["id"]))
        )
        if len(rows) < _RECONCILE_PAGE:
            break
        after = str(rows[-1]["id"])
    if not stale:
        return 0

    holds: Any = _build_worker_legal_holds()
    deleted = 0
    for doc_id in stale:
        if holds is not None:
            try:
                held = await holds.is_under_hold(
                    tenant_id=config.tenant_id, resource_id=doc_id
                ) or await holds.is_under_hold(
                    tenant_id=config.tenant_id, resource_id=config.collection_id
                )
            except Exception as exc:
                _log.warning(
                    "upstream_deletion_hold_unverifiable source=%s doc=%s: %s",
                    config.source_id, doc_id, exc,
                )
                continue
            if held:
                continue
        if await store.delete_document_async(
            doc_id, collection_id=config.collection_id, tenant_ctx=tenant_ctx
        ):
            deleted += 1
    _log.info(
        "upstream_deletions_applied source=%s deleted=%d stale=%d",
        config.source_id, deleted, len(stale),
    )
    return deleted


async def _sync_source_async(
    *,
    task,
    source_id: str,
    tenant_id: str,
    triggered_by: str,
    job_id: str | None = None,
    reindex: bool = False,
) -> dict:
    """Async body of sync_source_task.

    ``job_id`` is set by ``POST /sources/{id}/sync``, which already took the
    source's lock (its token is the job id) so it can answer "already running";
    the task adopts that lock instead of acquiring it, and releases it at the end.
    """
    from app.ingestion.connector_registry import get_connector, load_all_connectors

    load_all_connectors()  # ensure the @register registry is populated in the worker
    tracker, pipeline, source_store = _build_worker_ingestion()

    # ── Distributed lock (LAW-14) ────────────────────────────────────────────
    lock_acquired = job_id or await tracker.acquire_lock(source_id, tenant_id, ttl_seconds=3600)
    if not lock_acquired:
        _log.info("source=%s already locked — skipping duplicate sync", source_id)
        return {"skipped": True, "reason": "already_running"}

    # ── Load SourceConfig (durable, cross-process store) ─────────────────────
    # The task carries its tenant, so this is a tenant-scoped (RLS) read — not a
    # system read. The previous ``get_system`` issued ``row_security = off`` on
    # the application's NOBYPASSRLS connection, where every statement then fails
    # ("query would be affected by row-level security"): no scheduled sync ran.
    config = await source_store.get(source_id, tenant_id)
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
    import uuid as _uuid

    try:
        connector_cls = get_connector(config.source_type)
    except (KeyError, RuntimeError) as exc:
        # The module failed to import / the type is unknown / its flag is off.
        # Record a failed job (the UI's sync status shows the reason) and free
        # the lock — this used to escape before the try/finally, holding it.
        from app.ingestion.connector_registry import connector_error_message

        message = connector_error_message(exc)
        try:
            failed_job = await tracker.create_job(
                config, job_id=job_id or str(_uuid.uuid4()), triggered_by=triggered_by
            )
            await tracker.complete_job(failed_job, error=message)
            await source_store.mark_synced(
                source_id, tenant_id, docs_indexed=0, chunks=0, failed=1
            )
        finally:
            await tracker.release_lock(source_id, tenant_id)
        return {"error": message}

    connector = connector_cls()

    # ── Create job record ────────────────────────────────────────────────────

    job = await tracker.create_job(
        config,
        job_id=job_id or str(_uuid.uuid4()),
        triggered_by=triggered_by,
    )

    docs_indexed = docs_failed = docs_skipped = 0
    cancelled = False

    try:
        if reindex:
            # "Delete + full re-sync": drop what this source indexed, then sync
            # from the beginning (dedup would otherwise skip unchanged documents
            # and nothing would be re-chunked or re-embedded).
            removed = await _delete_source_documents(pipeline, config)
            _log.info("reindex source=%s removed_documents=%d", source_id, removed)
            config.cursor_value = ""
            await source_store.update(source_id, tenant_id, cursor_value="")

        # ── Delta loop ───────────────────────────────────────────────────────
        cursor = config.cursor_value or None
        new_cursor = cursor

        async for raw_doc, next_cursor in connector.get_delta(config, cursor):  # type: ignore[misc]
            # KB-15: an operator cancel (POST /sources/{id}/sync/cancel) stops the
            # loop between documents; indexed work and the cursor are kept.
            if await tracker.is_cancel_requested(tenant_id, job.job_id) is True:
                cancelled = True
                break
            try:
                from app.core.config import get_settings
                from app.tenancy.context import PlanTier, TenantContext

                _settings = get_settings()
                _tenant_ctx = TenantContext(
                    tenant_id=tenant_id,
                    api_key_id="scheduler",
                    plan=PlanTier.FREE,
                )

                result = await pipeline.ingest(raw_doc, config)

                # PipelineResult exposes a ``status`` string, not success/skipped
                # booleans — the old attributes raised AttributeError on the first
                # document, killing every scheduled sync.
                # Tokens/chunks feed ingestion_jobs → GET /ingestion/cost.
                job.tokens_consumed += int(getattr(result, "tokens_consumed", 0) or 0)
                job.chunks_created += int(getattr(result, "chunks_created", 0) or 0)
                if result.status == "indexed":
                    docs_indexed += 1
                elif result.status == "skipped":
                    docs_skipped += 1
                else:
                    docs_failed += 1
                    _log.warning(
                        "pipeline failed: source=%s doc=%s error=%s",
                        source_id,
                        raw_doc.doc_id,
                        getattr(result, "error", None) or getattr(result, "skip_reason", ""),
                    )
                    # DLQ (LAW-17)
                    await tracker.add_to_dlq(
                        source_id=source_id,
                        tenant_id=tenant_id,
                        doc_id=raw_doc.doc_id,
                        error=getattr(result, "error", None)
                        or getattr(result, "skip_reason", "")
                        or "pipeline_failure",
                        raw_doc=raw_doc,
                        job_id=job.job_id,
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
        await tracker.complete_job(job, cancelled=cancelled)
        # Advance last_synced_at + cursor on the durable source row so the beat
        # due-scan reschedules the next sync one interval out (item 6).
        # mark_synced is the single owner of consecutive_failures (0 when no doc
        # failed, +1 otherwise) — the same rule the manual-sync path uses. The
        # tracker's separate reset/increment calls were dropped: their SQL named
        # a nonexistent column and never ran, and once fixed they would have
        # double-counted every failure against the backoff.
        await source_store.mark_synced(
            source_id,
            tenant_id,
            docs_indexed=docs_indexed,
            chunks=job.chunks_created,
            failed=docs_failed,
        )
        if new_cursor and new_cursor != (config.cursor_value or ""):
            await source_store.update(source_id, tenant_id, cursor_value=new_cursor)

        # Upstream deletions: only after a complete, failure-free run, and only
        # for connectors that can list what exists upstream.
        docs_deleted = 0
        if not docs_failed and not cancelled:
            docs_deleted = await _reconcile_upstream_deletions(connector, config, pipeline)

        return {
            "job_id": job.job_id,
            "docs_indexed": docs_indexed,
            "docs_skipped": docs_skipped,
            "docs_failed": docs_failed,
            "docs_deleted": docs_deleted,
            "cancelled": cancelled,
        }

    except Exception as exc:
        _log.exception("sync failed for source=%s: %s", source_id, exc)
        job.docs_indexed = docs_indexed
        job.docs_skipped = docs_skipped
        job.docs_failed = docs_failed
        await tracker.complete_job(job, error=str(exc))
        await source_store.mark_synced(
            source_id, tenant_id, docs_indexed=docs_indexed, chunks=0, failed=1
        )
        # Celery retry
        # The retry acquires the lock itself (this run releases it below).
        raise task.retry(
            exc=exc,
            countdown=int(_backoff_seconds(1)),
            kwargs={"source_id": source_id, "tenant_id": tenant_id, "triggered_by": triggered_by},
        ) from exc

    finally:
        await tracker.release_lock(source_id, tenant_id)


@shared_task(name="ingestion.reap_stale_jobs", bind=True)
def reap_stale_jobs_task(self) -> dict:
    """Celery beat: fail ingestion jobs a lost worker left ``running`` (KB-SYNC-WORKER)."""
    return _run_task_loop(_reap_stale_jobs_async())


async def _reap_stale_jobs_async(*, tracker: object | None = None) -> dict:
    from app.core.config import get_settings
    from app.ingestion.job_tracker import IngestionJobTracker

    age = max(3600, int(getattr(get_settings(), "ingestion_stale_job_seconds", 7200)))
    if tracker is None:
        from app.db.session import get_system_session_factory

        tracker = IngestionJobTracker(system_db=get_system_session_factory())
    reaped = await tracker.reap_stale_jobs(older_than_seconds=age)  # type: ignore[attr-defined]
    for row in reaped:
        _log.warning(
            "ingestion_job_reaped job=%s tenant=%s source=%s",
            row["id"], row["tenant_id"], row["source_id"],
        )
    return {"reaped": len(reaped), "older_than_seconds": age}


@shared_task(name="ingestion.dispatch_due_sources", bind=True)
def dispatch_due_sources_task(self) -> dict:
    """Celery beat entry point — find all sources due for sync and enqueue them.

    Runs every minute via beat schedule. Each source gets an individual
    sync_source_task with per-source jitter.
    """
    return _run_task_loop(_dispatch_due_sources_async())


async def _dispatch_due_sources_async() -> dict:
    """Find sources whose next sync is due and enqueue sync tasks."""
    import datetime

    from app.db.session import get_system_session_factory
    from app.ingestion.source_store import SourceConfigStore

    # DB-backed, cross-tenant scan of the durable source_configs table (the old
    # in-memory IngestionJobTracker().get_due_sources() always returned []).
    # Cross-tenant → the maintenance role. On the application's NOBYPASSRLS
    # factory (which this used before) ``system_session`` makes every statement
    # fail, so nothing was ever dispatched. Each enqueued sync then runs for its
    # own tenant under RLS.
    source_store = SourceConfigStore(system_db=get_system_session_factory())
    due_sources = await source_store.list_due()

    dispatched = 0
    for source_id, tenant_id in due_sources:
        jitter = _jitter(source_id)
        sync_source_task.apply_async(
            kwargs={"source_id": source_id, "tenant_id": tenant_id, "triggered_by": "scheduler"},
            countdown=jitter,
        )
        dispatched += 1

    _log.info("dispatch_due_sources: dispatched=%d sources", dispatched)
    return {
        "dispatched": dispatched,
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


@shared_task(name="ingestion.retry_dlq_entries", bind=True)
def retry_dlq_entries_task(self) -> dict:
    """Retry eligible DLQ entries (exponential backoff, max 5 attempts)."""
    return _run_task_loop(_retry_dlq_async())


async def _retry_dlq_async() -> dict:
    """Pull eligible DLQ entries and resubmit through the pipeline.

    The scan is cross-tenant (maintenance role, inside the tracker); everything
    done for one entry — loading its Source, re-ingesting, updating the row — is
    that entry's tenant's work and runs under its RLS context.

    This previously built ``IngestionJobTracker()`` with no DB (so the scan
    always returned []), an ``IngestionPipeline()`` with no knowledge store or
    embedder, read DB rows as attributes, and passed no SourceConfig — so even a
    returned entry could only ever be skipped.
    """
    tracker, pipeline, source_store = _build_worker_ingestion()

    entries = await tracker.get_retryable_dlq_entries(max_entries=50)
    retried = succeeded = still_failed = 0

    for entry in entries:
        outcome = await _retry_one_dlq_entry(entry, tracker, pipeline, source_store)
        if outcome in ("succeeded", "still_failed"):
            retried += 1
        if outcome == "succeeded":
            succeeded += 1
        elif outcome == "still_failed":
            still_failed += 1

    _log.info(
        "retry_dlq: retried=%d succeeded=%d still_failed=%d", retried, succeeded, still_failed
    )
    return {"retried": retried, "succeeded": succeeded, "still_failed": still_failed}


async def _retry_one_dlq_entry(
    entry: dict, tracker, pipeline, source_store, *, force: bool = False
) -> str:
    """Replay one DLQ entry: ``succeeded`` | ``still_failed`` | ``permanent`` | ``skipped``.

    ``force`` (an operator retry) replays an entry even at or past the retry cap.
    """
    from app.ingestion.job_tracker import raw_document_from_dlq_json

    dlq_id = str(entry.get("dlq_id") or "")
    tenant_id = str(entry.get("tenant_id") or "")
    source_id = str(entry.get("source_id") or "")
    if not dlq_id or not tenant_id:
        return "skipped"

    if not force and int(entry.get("retry_count") or 0) >= _DLQ_MAX_RETRIES:
        await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
        return "permanent"

    repository_payload = _repository_dlq_payload(entry.get("raw_doc_json"))
    if repository_payload is not None:
        # A failed repository ingestion: replay it as a new durable job
        # (these rows used to be marked permanent — nothing could replay them).
        from app.ingestion.repo_tasks import replay_repository_dlq_entry

        try:
            replayed = await replay_repository_dlq_entry(
                repository_payload, tenant_id=tenant_id, max_attempts=_DLQ_MAX_RETRIES
            )
        except Exception as exc:
            await tracker.increment_dlq_retry(dlq_id, tenant_id, error=str(exc)[:300])
            return "still_failed"
        if replayed:
            await tracker.resolve_dlq_entry(dlq_id, tenant_id)
            return "succeeded"
        await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
        return "permanent"

    raw_doc = raw_document_from_dlq_json(
        entry.get("raw_doc_json"),
        source_id=source_id,
        tenant_id=tenant_id,
        doc_id=str(entry.get("doc_id") or ""),
    )
    if raw_doc is None:
        # Not a connector document (e.g. repo-ingest parameters) or an
        # unreadable payload: no retry can replay it, so stop rescanning it.
        _log.warning("retry_dlq: dlq=%s has no replayable document", dlq_id)
        await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
        return "permanent"

    from app.ingestion.source_config import CONNECTOR_FAILURE_RETRYABLE_KEY

    if (raw_doc.metadata or {}).get(CONNECTOR_FAILURE_RETRYABLE_KEY) is False:
        # The connector said no retry can fix this (object deleted, access
        # denied, over the size cap): stop rescanning it.
        _log.info("retry_dlq: dlq=%s is a permanent connector failure", dlq_id)
        await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
        return "permanent"

    try:
        config = await source_store.get(source_id, tenant_id)
        if config is None:
            # The Source was deleted: nothing can ever replay this entry.
            await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
            return "still_failed"
        from app.ingestion.source_config import CONNECTOR_REPLAY_KEY

        reference = (raw_doc.metadata or {}).get(CONNECTOR_REPLAY_KEY)
        if isinstance(reference, dict):
            # A failed event fetch: replaying the empty failure document can never
            # succeed — ask the connector to fetch the item again instead.
            return await _replay_connector_event(
                dlq_id, tenant_id, config, reference, tracker, pipeline
            )
        result = await pipeline.run(raw_doc, source_config=config)
        # ``dedup`` means the content IS indexed (e.g. a concurrent sync
        # got there first) — that resolves the entry, it is not a failure.
        if result.success or (result.skipped and result.skip_reason == "dedup"):
            await tracker.resolve_dlq_entry(dlq_id, tenant_id)
            return "succeeded"
        await tracker.increment_dlq_retry(
            dlq_id,
            tenant_id,
            error=result.error or result.skip_reason or result.status,
        )
        return "still_failed"
    except Exception as exc:
        await tracker.increment_dlq_retry(dlq_id, tenant_id, error=str(exc))
        return "still_failed"


async def _replay_connector_event(
    dlq_id: str,
    tenant_id: str,
    config: Any,
    reference: dict[str, Any],
    tracker: Any,
    pipeline: Any,
) -> str:
    """Re-fetch the item a failed connector event named and run it through the pipeline.

    ``succeeded`` when every re-fetched document is indexed (or already was);
    ``permanent`` when the connector now reports a failure no retry can fix;
    ``still_failed`` otherwise (the retry count and backoff advance).
    """
    from app.ingestion.connector_registry import get_connector, load_all_connectors
    from app.ingestion.source_config import (
        CONNECTOR_FAILURE_KEY,
        CONNECTOR_FAILURE_RETRYABLE_KEY,
    )

    load_all_connectors()  # idempotent
    connector = get_connector(config.source_type)()
    docs = [doc async for doc in connector.replay_event(config, reference)]
    if not docs:
        await tracker.increment_dlq_retry(dlq_id, tenant_id, error="replay fetched nothing")
        return "still_failed"
    for doc in docs:
        meta = doc.metadata or {}
        if CONNECTOR_FAILURE_KEY in meta:
            if meta.get(CONNECTOR_FAILURE_RETRYABLE_KEY) is False:
                _log.info("retry_dlq: dlq=%s replay failed permanently", dlq_id)
                await tracker.mark_dlq_permanent_failure(dlq_id, tenant_id)
                return "permanent"
            await tracker.increment_dlq_retry(
                dlq_id, tenant_id, error=str(meta[CONNECTOR_FAILURE_KEY])[:300]
            )
            return "still_failed"
    errors: list[str] = []
    for doc in docs:
        result = await pipeline.run(doc, source_config=config)
        if not (result.success or (result.skipped and result.skip_reason == "dedup")):
            errors.append(result.error or result.skip_reason or result.status)
    if errors:
        await tracker.increment_dlq_retry(dlq_id, tenant_id, error="; ".join(errors)[:300])
        return "still_failed"
    await tracker.resolve_dlq_entry(dlq_id, tenant_id)
    return "succeeded"


@shared_task(name="ingestion.retry_dlq_entry", bind=True)
def retry_dlq_entry_task(self, *, dlq_id: str, tenant_id: str) -> dict:
    """Operator retry of ONE DLQ entry (POST /ingestion/dlq/{id}/retry)."""
    return _run_task_loop(
        _retry_dlq_entry_async(dlq_id=dlq_id, tenant_id=tenant_id)
    )


async def _retry_dlq_entry_async(*, dlq_id: str, tenant_id: str) -> dict:
    """Load the entry under its tenant's RLS context and replay it now (``force``)."""
    tracker, pipeline, source_store = _build_worker_ingestion()
    entry = await tracker.get_dlq_entry(dlq_id, tenant_id)
    if entry is None:
        return {"dlq_id": dlq_id, "outcome": "not_found"}
    if entry.get("resolved_at") is not None:
        return {"dlq_id": dlq_id, "outcome": "already_resolved"}
    outcome = await _retry_one_dlq_entry(entry, tracker, pipeline, source_store, force=True)
    _log.info("retry_dlq_entry: dlq=%s outcome=%s", dlq_id, outcome)
    return {"dlq_id": dlq_id, "outcome": outcome}


_REINDEX_PAGE = 200


async def _delete_source_documents(pipeline, config) -> int:
    """Delete every document a Source indexed into its collection (for reindex).

    Documents under an in-force legal hold (on the document, its collection or
    the whole tenant) are kept — one hold query per page, keyset-paginated so
    kept documents are not re-read. A hold check that fails raises, so the
    reindex stops instead of deleting what it could not verify (fail closed).
    """
    store = getattr(pipeline, "_kb", None)
    if store is None or not config.collection_id:
        return 0
    from app.tenancy.context import PlanTier, TenantContext

    tenant_ctx = TenantContext(
        tenant_id=config.tenant_id, api_key_id="reindex", plan=PlanTier.FREE
    )
    removed = kept = 0
    after: str | None = None
    while True:
        documents = await store.list_source_documents_async(
            tenant_ctx=tenant_ctx,
            collection_id=config.collection_id,
            source_id=config.source_id,
            limit=_REINDEX_PAGE,
            after=after,
        )
        if not documents:
            break
        ids = [str(document["id"]) for document in documents]
        held = await store.held_document_ids_async(
            config.collection_id, ids, tenant_ctx=tenant_ctx
        )
        for doc_id in ids:
            if doc_id in held:
                kept += 1
                continue
            await store.delete_document_async(
                doc_id, collection_id=config.collection_id, tenant_ctx=tenant_ctx
            )
            removed += 1
        if len(documents) < _REINDEX_PAGE:
            break
        after = ids[-1]
    if kept:
        _log.info(
            "reindex_kept_held_documents source=%s kept=%d", config.source_id, kept
        )
    return removed


def _repository_dlq_payload(raw_doc_json: object) -> dict | None:
    """The replay parameters of a dead-lettered repository ingestion, or None."""
    import json

    if not isinstance(raw_doc_json, str) or not raw_doc_json:
        return None
    try:
        payload = json.loads(raw_doc_json)
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("kind") == "repository":
        return payload
    return None


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
