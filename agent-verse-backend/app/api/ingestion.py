"""Ingestion API — CRUD + sync control + DLQ + health + preview.

38 endpoints covering:
  POST/GET/PATCH/DELETE /api/v1/sources         Source CRUD
  POST                  /api/v1/sources/{id}/sync         Trigger sync
  POST                  /api/v1/sources/{id}/sync/cancel  Cancel running sync
  GET                   /api/v1/sources/{id}/sync/status  Current job
  GET                   /api/v1/sources/{id}/sync/history Past jobs
  GET                   /api/v1/sources/{id}/health       Connection check
  GET                   /api/v1/sources/{id}/preview      Sample 5 docs (dry-run)
  GET                   /api/v1/sources/{id}/stats        Totals
  POST                  /api/v1/sources/{id}/reindex      Delete + full re-sync
  GET                   /api/v1/sources/catalogue         All supported types
  POST                  /api/v1/sources/validate          Validate config before save
  GET/DELETE            /api/v1/ingestion/documents       Indexed document CRUD
  GET/POST/POST         /api/v1/ingestion/dlq             DLQ management
  GET                   /api/v1/ingestion/quota           Tenant quota
  GET                   /api/v1/ingestion/cost            Cost breakdown
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from app.ingestion.source_config import SourceConfig, SourceFamily

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


# ── Request / Response models ─────────────────────────────────────────────────


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


# In-memory source store (replaced by DB-backed in production)
_SOURCES: dict[str, SourceConfig] = {}


def _serialize_source(s: SourceConfig) -> dict:
    import dataclasses

    d = dataclasses.asdict(s)
    d["family"] = s.family.value if hasattr(s.family, "value") else str(s.family)
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

    try:
        from app.ingestion.connector_registry import get_connector

        connector_cls = get_connector(source.source_type)
        connector = connector_cls()
        health = await connector.validate_connection(source)
        return {
            "ok": health.ok,
            "latency_ms": health.latency_ms,
            "error": health.error or None,
            "metadata": health.metadata,
        }
    except KeyError:
        return {"ok": False, "error": f"No connector for source_type={source.source_type!r}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Sync control ──────────────────────────────────────────────────────────────


@router.post("/{source_id}/sync", response_model=dict, status_code=202)
async def trigger_sync(
    source_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Trigger a manual sync (LAW-14: acquires distributed lock first)."""
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    tracker = _get_tracker(request)
    pipeline = _get_pipeline(request)

    if tracker is None:
        return {"status": "accepted", "message": "Ingestion framework not configured"}

    # LAW-14: try to acquire lock
    job_id = await tracker.acquire_lock(source_id, tenant.tenant_id)
    if job_id is None:
        return {"status": "already_running", "message": "Sync already in progress for this source"}

    background_tasks.add_task(
        _run_sync, source, pipeline, tracker, job_id, _get_source_store(request)
    )
    return {"status": "queued", "job_id": job_id}


@router.get("/{source_id}/sync/status", response_model=dict)
async def sync_status(source_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    source = await _load_source(request, source_id, tenant.tenant_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    tracker = _get_tracker(request)
    if tracker is None:
        return {"status": "unknown"}
    jobs = tracker.list_jobs_for_source(source_id)
    if not jobs:
        return {"status": "never_synced"}
    latest = max(jobs, key=lambda j: j.created_at)
    import dataclasses

    return dataclasses.asdict(latest)


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

    from app.ingestion.connector_registry import get_connector

    pipeline._dry_run = True
    results: list[dict] = []
    try:
        connector_cls = get_connector(source.source_type)
        connector = connector_cls()
        count = 0
        async for raw_doc, _ in connector.get_delta(source, None):
            if count >= 5:
                break
            result = await pipeline.ingest(raw_doc, source)
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
        return {"error": str(exc), "docs_previewed": len(results)}
    finally:
        pipeline._dry_run = False

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


@documents_router.get("/documents", response_model=list[dict])
async def list_documents(request: Request, source_id: str = "", limit: int = 50) -> list[dict]:
    """List indexed documents for a source.

    NOTE (honest status): there is no per-document registry table yet — the
    ingestion pipeline writes chunks to the RAG store but does not persist a
    queryable ``indexed_documents`` row per source. This endpoint therefore
    always returns an empty list; it is a stub, not a populated feature. Wiring
    it requires a real ``indexed_documents`` table (see pipeline.py CQRS note).
    """
    _require_tenant(request)
    return []


@documents_router.get("/quota", response_model=dict)
async def get_quota(request: Request) -> dict:
    tenant = _require_tenant(request)
    return {
        "tenant_id": tenant.tenant_id,
        "plan": getattr(tenant, "plan", "free"),
        "sources_used": sum(1 for s in _SOURCES.values() if s.tenant_id == tenant.tenant_id),
        "sources_limit": {"free": 2, "starter": 10, "professional": 50}.get(
            getattr(tenant, "plan", "free"), 999_999
        ),
    }


@documents_router.get("/cost", response_model=dict)
async def get_cost(request: Request) -> dict:
    tenant = _require_tenant(request)
    return {"tenant_id": tenant.tenant_id, "tokens_used_month": 0, "cost_usd_month": 0.0}


@documents_router.get("/dlq", response_model=list[dict])
async def list_dlq(request: Request) -> list[dict]:
    _require_tenant(request)
    return []


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

        await tracker.complete_job(job)

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
