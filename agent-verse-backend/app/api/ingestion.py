"""Ingestion API — Source CRUD, sync control, DLQ, health, preview, quota.

Endpoints (this list is checked against the registered routes by a test):
  GET/POST      /sources                              List / create Sources
  GET           /sources/catalogue                    All supported connector types
  POST          /sources/validate                     Validate a config before saving it
  GET/PATCH/DELETE /sources/{source_id}               Read / update / delete a Source
  GET           /sources/{source_id}/health           Connection check
  POST          /sources/{source_id}/sync             Trigger a sync (durable Celery task)
  POST          /sources/{source_id}/sync/cancel      Cancel the running sync
  GET           /sources/{source_id}/sync/status      Latest sync job
  GET           /sources/{source_id}/sync/history     Recent sync jobs
  POST          /sources/{source_id}/reindex          Delete the Source's documents + full re-sync
  POST          /sources/{source_id}/preview          Sample 5 docs (dry-run)
  GET           /sources/{source_id}/stats            Totals
  GET           /ingestion/documents                  Indexed documents of a Source
  GET           /ingestion/dlq                        DLQ listing
  POST          /ingestion/dlq/{dlq_id}/retry         Retry one DLQ entry now
  GET           /ingestion/quota                      Tenant quota
  GET           /ingestion/cost                       Cost breakdown
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.observability.logging import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/sources", tags=["ingestion"])
documents_router = APIRouter(prefix="/ingestion", tags=["ingestion"])


# ── Dependency helpers ────────────────────────────────────────────────────────


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx


def _get_pipeline(request: Request) -> Any:
    return getattr(request.app.state, "ingestion_pipeline", None)


def _get_tracker(request: Request) -> Any:
    return getattr(request.app.state, "ingestion_job_tracker", None)


def _get_source_store(request: Request) -> Any:
    """Durable Source store (DB-backed in prod). None → legacy in-memory dict."""
    return getattr(request.app.state, "ingestion_source_store", None)


async def _load_source(request: Request, source_id: str, tenant_id: str) -> Any:
    store = _get_source_store(request)
    if store is not None:
        return await store.get(source_id, tenant_id)
    src = _SOURCES.get(source_id)
    return src if (src and src.tenant_id == tenant_id) else None


async def _refuse_reindex_of_held_collection(
    request: Request, tenant_id: str, collection_id: str | None
) -> None:
    """409 when the Source's collection (or the whole tenant) is under legal hold.

    A reindex deletes the Source's documents; with the collection held none of
    them may go. Held documents inside an unheld collection are skipped by the
    worker. Fail closed: an unverifiable hold state refuses the reindex (503).
    """
    if not collection_id:
        return
    holds = getattr(request.app.state, "legal_hold_manager", None)
    if holds is None:
        # A persistent app without its hold manager cannot know what is held.
        if getattr(request.app.state, "manage_pools", False):
            raise HTTPException(
                status_code=503, detail="Legal hold state could not be verified; reindex refused"
            )
        return
    try:
        held = await holds.is_under_hold(tenant_id=tenant_id, resource_id=collection_id)
    except Exception as exc:
        _log.exception("ingestion_reindex_hold_check_failed", collection_id=collection_id)
        raise HTTPException(
            status_code=503, detail="Legal hold state could not be verified; reindex refused"
        ) from exc
    if held:
        raise HTTPException(
            status_code=409,
            detail="The source's collection is under legal hold; reindex would delete held data",
        )


# ── Request / Response models ─────────────────────────────────────────────────


def _validate_chunking_strategy(value: str | None) -> str | None:
    """422 for a chunking strategy with no implementation (it used to be accepted
    and silently chunked as ``semantic``)."""
    from app.ingestion.chunkers import SUPPORTED_CHUNKING_STRATEGIES

    if value is not None and value not in SUPPORTED_CHUNKING_STRATEGIES:
        raise ValueError(
            f"unsupported chunking_strategy {value!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_CHUNKING_STRATEGIES))}"
        )
    return value


class CreateSourceRequest(BaseModel):
    name: str = Field(..., min_length=1)
    family: str
    source_type: str
    connection_config: dict = {}
    sync_mode: str = "incremental"
    sync_interval_seconds: int = 3600
    chunking_strategy: str = "auto"
    embedding_model: str = "auto"
    collection_id: str = ""
    pii_action: str = "redact"
    min_quality_score: float = 0.3
    tags: list[str] = []
    include_patterns: list[str] = []
    exclude_patterns: list[str] = []

    model_config = {"extra": "allow"}

    _chunking_strategy_supported = field_validator("chunking_strategy")(
        _validate_chunking_strategy
    )


class UpdateSourceRequest(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    sync_mode: str | None = None
    sync_interval_seconds: int | None = None
    chunking_strategy: str | None = None
    collection_id: str | None = None
    enabled: bool | None = None
    pii_action: str | None = None
    tags: list[str] | None = None

    model_config = {"extra": "allow"}

    _chunking_strategy_supported = field_validator("chunking_strategy")(
        _validate_chunking_strategy
    )


# In-memory source store (replaced by DB-backed in production)
_SOURCES: dict[str, SourceConfig] = {}


def _serialize_source(s: SourceConfig) -> dict:
    import dataclasses

    from app.ingestion.source_secrets import mask_connection_config

    d = dataclasses.asdict(s)
    d["family"] = s.family.value if hasattr(s.family, "value") else str(s.family)
    # Credentials never leave the API — not in plaintext (as they used to on every
    # GET/POST/PATCH) and not encrypted either. Secret values are masked and the
    # client gets a has_credentials flag; PATCHing the mask back keeps the secret.
    d["connection_config"], d["has_credentials"] = mask_connection_config(
        d.get("connection_config") or {}
    )
    return d


# ── Source catalogue ──────────────────────────────────────────────────────────
# NOTE: this static route MUST be registered before the dynamic
# GET /sources/{source_id} route below — Starlette matches routes in
# registration order, so if this were registered after, requests to
# /sources/catalogue would be swallowed by /sources/{source_id} (with
# source_id="catalogue") and 404 instead of returning the catalogue.


@router.get("/catalogue", response_model=list[dict], include_in_schema=True)
async def get_catalogue(request: Request) -> list[dict]:
    """Return all registered connector types for the UI source picker."""
    from app.ingestion.connector_registry import get_connector_metadata

    return get_connector_metadata()


@router.post("/validate", response_model=dict)
async def validate_source(
    request: Request,
    body: CreateSourceRequest,
    check_connection: bool = Query(default=True),
) -> dict:
    """Validate a Source config before saving it; nothing is persisted.

    Field-level problems (e.g. an unsupported chunking strategy) are a 422 like
    on create. Semantic problems are reported in ``errors`` — unknown family, no
    connector for ``source_type`` — and, unless ``check_connection=false``, the
    connector's own connection probe runs against the unsaved config.
    """
    tenant = _require_tenant(request)
    errors: list[str] = []
    try:
        family = SourceFamily(body.family)
    except ValueError:
        errors.append(f"Unknown family: {body.family!r}")
        family = None
    connector_cls: Any = None
    try:
        from app.ingestion.connector_registry import get_connector

        connector_cls = get_connector(body.source_type)
    except KeyError:
        connector_cls = None
    if connector_cls is None:
        errors.append(f"No connector for source_type={body.source_type!r}")
    connection: dict | None = None
    if not errors and check_connection and family is not None:
        config = SourceConfig(
            source_id=f"validate-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant.tenant_id,
            name=body.name,
            family=family,
            source_type=body.source_type,
            connection_config=body.connection_config,
            sync_mode=body.sync_mode,
            collection_id=body.collection_id,
            include_patterns=body.include_patterns,
            exclude_patterns=body.exclude_patterns,
        )
        try:
            health = await connector_cls().validate_connection(config)
            connection = {
                "ok": bool(health.ok),
                "latency_ms": health.latency_ms,
                "error": health.error or None,
            }
        except Exception as exc:
            connection = {"ok": False, "latency_ms": None, "error": str(exc)[:300]}
        if not connection["ok"]:
            errors.append(f"Connection check failed: {connection['error'] or 'unknown error'}")
    return {"valid": not errors, "errors": errors, "connection": connection}


# ── Sources CRUD ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[dict])
async def list_sources(request: Request) -> list[dict]:
    tenant = _require_tenant(request)
    store = _get_source_store(request)
    if store is not None:
        return [_serialize_source(s) for s in await store.list(tenant.tenant_id)]
    return [_serialize_source(s) for s in _SOURCES.values() if s.tenant_id == tenant.tenant_id]


@router.post("", response_model=dict, status_code=201)
async def create_source(request: Request, body: CreateSourceRequest) -> dict:
    tenant = _require_tenant(request)
    try:
        family = SourceFamily(body.family)
    except ValueError as _b904_exc:
        raise HTTPException(status_code=422, detail=f"Unknown family: {body.family!r}") from _b904_exc  # noqa: E501

    # Source quota (plan limit) — counted in the DB; it was never enforced.
    enforcer = _get_quota_enforcer(request)
    if enforcer is not None:
        from app.ingestion.quota import IngestionQuotaExceededError

        try:
            await enforcer.check_source_quota(tenant.tenant_id, plan=_plan_of(tenant))
        except IngestionQuotaExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except Exception as exc:
            _log.exception("ingestion_source_quota_check_failed")
            raise HTTPException(status_code=503, detail="Quota check is unavailable") from exc

    source_id = uuid.uuid4().hex
    config = SourceConfig(
        source_id=source_id,
        tenant_id=tenant.tenant_id,
        name=body.name,
        family=family,
        source_type=body.source_type,
        connection_config=body.connection_config,
        sync_mode=body.sync_mode,
        sync_interval_seconds=body.sync_interval_seconds,
        chunking_strategy=body.chunking_strategy,
        embedding_model=body.embedding_model,
        collection_id=body.collection_id,
        pii_action=body.pii_action,
        min_quality_score=body.min_quality_score,
        tags=body.tags,
        include_patterns=body.include_patterns,
        exclude_patterns=body.exclude_patterns,
    )
    store = _get_source_store(request)
    if store is not None:
        await store.create(config)
    else:
        _SOURCES[source_id] = config
    return _serialize_source(config)


@router.get("/{source_id}", response_model=dict)
async def get_source(source_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return _serialize_source(source)


@router.patch("/{source_id}", response_model=dict)
async def update_source(source_id: str, request: Request, body: UpdateSourceRequest) -> dict:
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    update_data = body.model_dump(exclude_none=True)
    if isinstance(update_data.get("connection_config"), dict):
        from app.ingestion.source_secrets import merge_masked_update

        # A masked secret echoed back from a GET means "unchanged".
        update_data["connection_config"] = merge_masked_update(
            source.connection_config, update_data["connection_config"]
        )
    store = _get_source_store(request)
    if store is not None:
        updated = await store.update(source_id, tenant.tenant_id, **update_data)
        return _serialize_source(updated or source)
    for key, val in update_data.items():
        if hasattr(source, key):
            setattr(source, key, val)
    return _serialize_source(source)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: str, request: Request) -> None:
    tenant = _require_tenant(request)
    store = _get_source_store(request)
    if store is not None:
        if not await store.delete(source_id, tenant.tenant_id):
            raise HTTPException(status_code=404, detail="Source not found")
        return
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    del _SOURCES[source_id]


# ── Health check ──────────────────────────────────────────────────────────────


@router.get("/{source_id}/health", response_model=dict)
async def health_check(source_id: str, request: Request) -> dict:
    """Test connection to the source (LAW-21: health probe per connector)."""
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from app.ingestion.connector_registry import connector_error_message

    try:
        connector_cls = _available_connector(source.source_type)
        connector = connector_cls()
        health = await connector.validate_connection(source)
        return {
            "ok": health.ok,
            "latency_ms": health.latency_ms,
            "error": health.error or None,
            "metadata": health.metadata,
        }
    except Exception as exc:
        return {"ok": False, "error": connector_error_message(exc)}


def _available_connector(source_type: str) -> Any:
    """The connector class for ``source_type``; raises KeyError/RuntimeError with an
    honest message (module failed to load, unknown type, disabled by flag)."""
    from app.ingestion.connector_registry import get_connector, load_all_connectors

    load_all_connectors()  # idempotent; records modules that fail to import
    return get_connector(source_type)


# ── Sync control ──────────────────────────────────────────────────────────────


@router.post("/{source_id}/sync", response_model=dict, status_code=202)
async def trigger_sync(source_id: str, request: Request) -> dict:
    """Trigger a manual sync (LAW-14: acquires distributed lock first).

    The sync runs as the durable ``ingestion.sync_source`` Celery task on the
    ingestion queue — it used to run in this API process's BackgroundTasks, so
    a restart or scale-down lost it with the job stuck ``running``.
    """
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Refuse up front when the connector cannot run here: queuing the job would
    # only fail later in the worker, where the UI could not see why.
    from app.ingestion.connector_registry import connector_error_message

    try:
        _available_connector(source.source_type)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=connector_error_message(exc)) from exc

    tracker = _get_tracker(request)

    if tracker is None:
        return {"status": "accepted", "message": "Ingestion framework not configured"}

    try:
        job_id = await enqueue_source_sync(tracker, source_id, tenant.tenant_id)
    except SyncEnqueueError as exc:
        raise HTTPException(
            status_code=503, detail="Sync could not be queued; try again shortly"
        ) from exc
    if job_id is None:
        return {"status": "already_running", "message": "Sync already in progress for this source"}
    return {"status": "queued", "job_id": job_id}


class SyncEnqueueError(RuntimeError):
    """The sync task could not be handed to the broker (the lock was released)."""


async def enqueue_source_sync(
    tracker: Any, source_id: str, tenant_id: str, *, triggered_by: str = "manual"
) -> str | None:
    """Take the source's lock and queue the durable ``ingestion.sync_source`` task.

    Returns the job id (the lock token the worker adopts), or None when a sync
    already holds the lock. Every manual sync path goes through here, so none
    runs inside an API process (a restart or scale-down used to lose it with the
    job stuck ``running``). Raises :class:`SyncEnqueueError` after releasing the
    lock when the broker refuses the task.
    """
    job_id = await tracker.acquire_lock(source_id, tenant_id)  # LAW-14
    if job_id is None:
        return None
    from app.ingestion.scheduler import sync_source_task

    try:
        sync_source_task.apply_async(
            kwargs={
                "source_id": source_id,
                "tenant_id": tenant_id,
                "triggered_by": triggered_by,
                "job_id": job_id,
            },
            queue="ingestion",
        )
    except Exception as exc:
        await tracker.release_lock(source_id, tenant_id, job_id)
        _log.exception("ingestion_sync_enqueue_failed", source_id=source_id)
        raise SyncEnqueueError(str(exc)) from exc
    return str(job_id)


@router.get("/{source_id}/sync/status", response_model=dict)
async def sync_status(source_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    tracker = _get_tracker(request)
    if tracker is None:
        return {"status": "unknown"}
    # Durable history: syncs run in Celery workers, so this process's in-memory
    # job map (which this used to read) never saw them — "never_synced" forever.
    try:
        jobs = await tracker.list_jobs(source_id, tenant.tenant_id, limit=1)
    except Exception as exc:
        _log.exception("ingestion_sync_status_failed", source_id=source_id)
        raise HTTPException(status_code=503, detail="Sync history is unavailable") from exc
    if not jobs:
        return {"status": "never_synced"}
    return dict(jobs[0])


@router.get("/{source_id}/sync/history", response_model=list[dict])
async def sync_history(
    source_id: str, request: Request, limit: int = Query(default=20, ge=1, le=200)
) -> list[dict]:
    """The Source's recent sync jobs, newest first."""
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    tracker = _get_tracker(request)
    if tracker is None:
        raise HTTPException(status_code=503, detail="Ingestion framework not configured")
    try:
        return [dict(j) for j in await tracker.list_jobs(source_id, tenant.tenant_id, limit=limit)]
    except Exception as exc:
        _log.exception("ingestion_sync_history_failed", source_id=source_id)
        raise HTTPException(status_code=503, detail="Sync history is unavailable") from exc


@router.post("/{source_id}/sync/cancel", response_model=dict, status_code=202)
async def cancel_sync(source_id: str, request: Request) -> dict:
    """Cancel the Source's running sync.

    The worker stops between documents: what it already indexed stays indexed
    and the cursor is committed, so the next sync resumes from there. 409 when
    no sync is running.
    """
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    tracker = _get_tracker(request)
    if tracker is None:
        raise HTTPException(status_code=503, detail="Ingestion framework not configured")
    try:
        job_id = await tracker.request_cancel(source_id, tenant.tenant_id)
    except Exception as exc:
        _log.exception("ingestion_sync_cancel_failed", source_id=source_id)
        raise HTTPException(status_code=503, detail="Sync could not be cancelled") from exc
    if job_id is None:
        raise HTTPException(status_code=409, detail="No sync is running for this source")
    return {"status": "cancelling", "job_id": job_id}


@router.post("/{source_id}/reindex", response_model=dict, status_code=202)
async def reindex_source(source_id: str, request: Request) -> dict:
    """Delete everything this Source indexed and re-sync it from the start.

    Runs as the durable ``ingestion.sync_source`` task with ``reindex=True``
    under the Source's sync lock (409 while another sync runs).
    """
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    tracker = _get_tracker(request)
    if tracker is None:
        raise HTTPException(status_code=503, detail="Ingestion framework not configured")
    await _refuse_reindex_of_held_collection(request, tenant.tenant_id, source.collection_id)
    job_id = await tracker.acquire_lock(source_id, tenant.tenant_id)
    if job_id is None:
        raise HTTPException(status_code=409, detail="A sync is already running for this source")
    from app.ingestion.scheduler import sync_source_task

    try:
        sync_source_task.apply_async(
            kwargs={
                "source_id": source_id,
                "tenant_id": tenant.tenant_id,
                "triggered_by": "reindex",
                "job_id": job_id,
                "reindex": True,
            },
            queue="ingestion",
        )
    except Exception as exc:
        await tracker.release_lock(source_id, tenant.tenant_id, job_id)
        _log.exception("ingestion_reindex_enqueue_failed", source_id=source_id)
        raise HTTPException(
            status_code=503, detail="Reindex could not be queued; try again shortly"
        ) from exc
    return {"status": "queued", "job_id": job_id}


# ── Preview (dry-run) ─────────────────────────────────────────────────────────


@router.post("/{source_id}/preview", response_model=dict)
async def preview_source(source_id: str, request: Request) -> dict:
    """Dry-run: parse + chunk without embedding (LAW-22)."""
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    pipeline = _get_pipeline(request)
    if pipeline is None:
        return {"error": "Ingestion pipeline not configured"}

    results: list[dict] = []
    try:
        connector_cls = _available_connector(source.source_type)
        connector = connector_cls()
        count = 0
        async for raw_doc, _ in connector.get_delta(source, None):
            if count >= 5:
                break
            # Per-call dry run: the pipeline is shared by every request on
            # this replica, so its mode must never be flipped (PREVIEW-RACE).
            result = await pipeline.ingest(raw_doc, source, dry_run=True)
            results.append(
                {
                    "doc_id": result.doc_id,
                    "status": result.status,
                    "chunks_would_create": result.chunks_created,
                    "tokens_estimate": result.tokens_consumed,
                }
            )
            count += 1
    except Exception as exc:
        from app.ingestion.connector_registry import connector_error_message

        return {"error": connector_error_message(exc), "docs_previewed": len(results)}

    return {"docs_previewed": len(results), "sample": results}


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get("/{source_id}/stats", response_model=dict)
async def source_stats(source_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return {
        "source_id": source_id,
        "total_docs_indexed": source.total_docs_indexed,
        "total_chunks": source.total_chunks,
        "last_synced_at": source.last_synced_at,
        "cursor_value": source.cursor_value,
    }


# ── Documents ─────────────────────────────────────────────────────────────────


def _get_quota_enforcer(request: Request) -> Any:
    """DB-backed ``IngestionQuotaEnforcer`` (wired in the lifespan), or None."""
    return getattr(request.app.state, "ingestion_quota", None)


def _plan_of(tenant: Any) -> str:
    plan = getattr(tenant, "plan", "free")
    return str(getattr(plan, "value", plan) or "free")


@documents_router.get("/documents", response_model=list[dict])
async def list_documents(
    request: Request,
    source_id: str = "",
    limit: int = Query(default=50, ge=1, le=500),
    after: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """Indexed documents of one Source, one row per document.

    Was a stub that always returned ``[]``. Now aggregated in SQL from the
    Source's collection chunk table under tenant RLS (keyset-paginated by
    document id via ``after``). 404 for an unknown Source; 5xx on failure — an
    empty list only ever means "no documents".
    """
    tenant = _require_tenant(request)
    if not source_id:
        raise HTTPException(status_code=422, detail="source_id is required")
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.collection_id:
        return []
    store = getattr(request.app.state, "knowledge_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")
    try:
        rows = await store.list_source_documents_async(
            tenant_ctx=tenant,
            collection_id=source.collection_id,
            source_id=source_id,
            limit=limit,
            after=after,
        )
    except Exception as exc:
        _log.exception("ingestion_documents_query_failed", source_id=source_id)
        raise HTTPException(status_code=503, detail="Document index is unavailable") from exc
    return list(rows)


@documents_router.get("/quota", response_model=dict)
async def get_quota(request: Request) -> dict:
    """Tenant ingestion quota — usage read from the database.

    Previously counted the API module's in-memory ``_SOURCES`` dict, which is
    always empty once the DB-backed Source store is wired (i.e. in production).
    """
    from app.ingestion.quota import DOCUMENT_LIMITS, SOURCE_LIMITS

    tenant = _require_tenant(request)
    plan = _plan_of(tenant)
    enforcer = _get_quota_enforcer(request)
    tracker = _get_tracker(request)
    if enforcer is not None:
        try:
            usage = await enforcer.usage(tenant.tenant_id, plan=plan)
        except Exception as exc:
            _log.exception("ingestion_quota_query_failed")
            raise HTTPException(status_code=503, detail="Quota usage is unavailable") from exc
        sources_used, docs_used = usage.sources_used, usage.documents_indexed
        sources_limit, docs_limit = usage.sources_limit, usage.documents_limit
    else:
        # No database (dev/tests): the in-process Source store is the truth.
        store = _get_source_store(request)
        if store is not None:
            sources_used = len(await store.list(tenant.tenant_id))
        else:
            sources_used = sum(1 for s in _SOURCES.values() if s.tenant_id == tenant.tenant_id)
        docs_used = 0
        ks = getattr(request.app.state, "knowledge_store", None)
        if ks is not None and hasattr(ks, "collection_counters_async"):
            counters = await ks.collection_counters_async(tenant_ctx=tenant)
            docs_used = sum(int(c["document_count"]) for c in counters)
        sources_limit = SOURCE_LIMITS.get(plan, SOURCE_LIMITS["free"])
        docs_limit = DOCUMENT_LIMITS.get(plan, DOCUMENT_LIMITS["free"])
    tokens_used = 0
    if tracker is not None and getattr(tracker, "_db", None) is not None:
        try:
            tokens_used = (await tracker.monthly_usage(tenant.tenant_id))["tokens"]
        except Exception as exc:
            _log.exception("ingestion_quota_usage_failed")
            raise HTTPException(status_code=503, detail="Quota usage is unavailable") from exc
    return {
        "tenant_id": tenant.tenant_id,
        "plan": plan,
        "sources_used": sources_used,
        "sources_limit": sources_limit,
        "docs_used": docs_used,
        "docs_limit": docs_limit,
        "tokens_used_month": tokens_used,
        "tokens_limit_month": None,  # no token quota is enforced
        "cost_usd_month": _estimate_cost_usd(tokens_used)[0],
    }


def _estimate_cost_usd(tokens: int) -> tuple[float | None, str | None]:
    """Embedding cost for *tokens* at the configured model's list price.

    ``None`` when the configured model has no known price — an honest "unknown"
    instead of a fabricated $0.
    """
    import os

    from app.embedding.router import BUILTIN_EMBEDDING_CONFIGS

    model = (
        os.getenv("EMBEDDING_MODEL", "").strip()
        or os.getenv("NVIDIA_EMBED_MODEL", "").strip()
    )
    candidates = [
        cfg for key, cfg in BUILTIN_EMBEDDING_CONFIGS.items() if model and model in (key, cfg.model)
    ]
    if not candidates:
        return None, model or None
    cfg = candidates[0]
    return round(tokens / 1000.0 * cfg.cost_per_1k, 6), f"{cfg.provider}/{cfg.model}"


@documents_router.get("/cost", response_model=dict)
async def get_cost(request: Request) -> dict:
    """This month's ingestion token usage (from ``ingestion_jobs``) and its cost.

    Was a stub that always answered zeros. Covers connector/scheduled/manual
    Source syncs (the jobs that record tokens); ``cost_usd_month`` is ``None``
    when the embedding model's price is unknown.
    """
    tenant = _require_tenant(request)
    tracker = _get_tracker(request)
    if tracker is None or getattr(tracker, "_db", None) is None:
        raise HTTPException(status_code=503, detail="Ingestion usage requires a database")
    try:
        usage = await tracker.monthly_usage(tenant.tenant_id)
    except Exception as exc:
        _log.exception("ingestion_cost_query_failed")
        raise HTTPException(status_code=503, detail="Ingestion usage is unavailable") from exc
    cost, pricing_model = _estimate_cost_usd(usage["tokens"])
    return {
        "tenant_id": tenant.tenant_id,
        "tokens_used_month": usage["tokens"],
        "cost_usd_month": cost,
        "pricing_model": pricing_model,
        "jobs_month": usage["jobs"],
        "docs_indexed_month": usage["docs_indexed"],
        "chunks_created_month": usage["chunks_created"],
        "bytes_processed_month": usage["bytes_processed"],
        "basis": "ingestion_jobs",
    }


@documents_router.get("/dlq", response_model=list[dict])
async def list_dlq(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    source_id: str = "",
    include_resolved: bool = False,
) -> list[dict]:
    """The tenant's ingestion dead-letter queue (unresolved by default).

    Was a stub that always returned ``[]`` although ``ingestion_dlq`` is written
    by every failed sync.
    """
    tenant = _require_tenant(request)
    tracker = _get_tracker(request)
    if tracker is None or getattr(tracker, "_db", None) is None:
        raise HTTPException(status_code=503, detail="Ingestion DLQ requires a database")
    try:
        return list(
            await tracker.list_dlq_entries(
                tenant.tenant_id,
                limit=limit,
                source_id=source_id,
                include_resolved=include_resolved,
            )
        )
    except Exception as exc:
        _log.exception("ingestion_dlq_query_failed")
        raise HTTPException(status_code=503, detail="Ingestion DLQ is unavailable") from exc


@documents_router.post("/dlq/{dlq_id}/retry", response_model=dict, status_code=202)
async def retry_dlq_entry(dlq_id: str, request: Request) -> dict:
    """Replay one of the tenant's DLQ entries now (even past the automatic retry cap).

    Queued as ``ingestion.retry_dlq_entry`` on the ingestion queue; the entry is
    resolved, or its retry count/error updated, by the worker. 404 unknown entry,
    409 already resolved, 503 without a database.
    """
    tenant = _require_tenant(request)
    tracker = _get_tracker(request)
    if tracker is None or getattr(tracker, "_db", None) is None:
        raise HTTPException(status_code=503, detail="Ingestion DLQ requires a database")
    try:
        entry = await tracker.get_dlq_entry(dlq_id, tenant.tenant_id)
    except Exception as exc:
        _log.exception("ingestion_dlq_lookup_failed")
        raise HTTPException(status_code=503, detail="Ingestion DLQ is unavailable") from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="DLQ entry not found")
    if entry.get("resolved_at") is not None:
        raise HTTPException(status_code=409, detail="DLQ entry is already resolved")
    from app.ingestion.scheduler import retry_dlq_entry_task

    try:
        retry_dlq_entry_task.apply_async(
            kwargs={"dlq_id": dlq_id, "tenant_id": tenant.tenant_id}, queue="ingestion"
        )
    except Exception as exc:
        _log.exception("ingestion_dlq_retry_enqueue_failed")
        raise HTTPException(
            status_code=503, detail="Retry could not be queued; try again shortly"
        ) from exc
    return {"status": "queued", "dlq_id": dlq_id}


# ── Background sync task ──────────────────────────────────────────────────────


async def _run_sync(
    source: SourceConfig,
    pipeline: Any,
    tracker: Any,
    job_id: str,
    source_store: Any = None,
) -> None:
    """Background task: run incremental sync for a source."""
    import logging

    _log = logging.getLogger(__name__)
    job = await tracker.create_job(source, job_id=job_id, triggered_by="manual")

    indexed = skipped = failed = chunks = 0
    cancelled = False
    # Captured before the loop runs: tracker.update_cursor() mutates
    # source.cursor_value in place, so comparing last_cursor against
    # source.cursor_value *after* the loop would always be equal (bug) —
    # the durable source_store would never receive the advanced cursor.
    original_cursor = source.cursor_value or ""
    last_cursor = original_cursor
    try:
        from app.ingestion.connector_registry import get_connector

        connector_cls = get_connector(source.source_type)
        connector = connector_cls()

        async for raw_doc, new_cursor in connector.get_delta(source, source.cursor_value or None):
            if await tracker.is_cancel_requested(source.tenant_id, job.job_id) is True:
                cancelled = True
                break
            if pipeline is not None:
                result = await pipeline.ingest(raw_doc, source)
                indexed += 1 if result.status == "indexed" else 0
                skipped += 1 if result.status == "skipped" else 0
                failed += 1 if result.status == "failed" else 0
                chunks += result.chunks_created
                await tracker.increment_counters(
                    job,
                    indexed=1 if result.status == "indexed" else 0,
                    skipped=1 if result.status == "skipped" else 0,
                    failed=1 if result.status == "failed" else 0,
                    chunks=result.chunks_created,
                    tokens=result.tokens_consumed,
                )
                await tracker.update_cursor(job, new_cursor, source)
                last_cursor = new_cursor or last_cursor

        await tracker.complete_job(job, cancelled=cancelled)

    except Exception as exc:
        _log.error("sync_error source=%s: %s", source.source_id, exc)
        failed += 1
        await tracker.complete_job(job, error=str(exc))
    finally:
        # Persist stats + advance last_synced_at/cursor so the beat due-scan
        # reschedules the next sync one interval out (item 6 durability).
        if source_store is not None:
            try:
                await source_store.mark_synced(
                    source.source_id, source.tenant_id,
                    docs_indexed=indexed, chunks=chunks, failed=failed,
                )
                if last_cursor and last_cursor != original_cursor:
                    await source_store.update(
                        source.source_id, source.tenant_id, cursor_value=last_cursor
                    )
            except Exception as _stat_exc:
                _log.warning("source_stats_update_failed: %s", _stat_exc)
        await tracker.release_lock(source.source_id, source.tenant_id, job_id)
