"""Multimodal Intelligence API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/multimodal", tags=["multimodal"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class IngestRequest(BaseModel):
    modality: str  # text | image | pdf | audio | video | code | table
    content: str | None = None  # For text/code/table (CSV or markdown table text)
    base64_data: str | None = None  # For binary types
    filename: str | None = None
    collection_id: str | None = None
    language: str | None = None  # For code, e.g. "python"


def _get_pipeline(request: Request) -> Any:
    """Prefer the DI'd, app.state-bound pipeline (persistent job store, real
    provider wired once at startup) over the bare module singleton, which is
    kept only for backward-compatible imports / lightweight test harnesses
    that build a router without going through create_app()."""
    from app.multimodal.pipeline import multimodal_pipeline

    return getattr(request.app.state, "multimodal_pipeline", None) or multimodal_pipeline


@router.post("/ingest")
async def ingest_asset(request: Request, body: IngestRequest) -> dict[str, Any]:
    """Ingest a multimodal asset and extract structured spans."""
    tenant = _require_tenant(request)
    pipeline = _get_pipeline(request)

    provider = getattr(request.app.state, "_app_provider", None)
    if provider:
        pipeline.set_provider(provider)

    tid = tenant.tenant_id
    cid = body.collection_id
    data = body.base64_data or ""

    modality_map = {
        "text": lambda: pipeline.ingest_text(body.content or "", tid, cid),
        "image": lambda: pipeline.ingest_image(data, tid, cid, body.filename),
        "pdf": lambda: pipeline.ingest_pdf(data, tid, cid, body.filename),
        "audio": lambda: pipeline.ingest_audio(data, tid, cid),
        "video": lambda: pipeline.ingest_video(data, tid, cid),
        "code": lambda: pipeline.ingest_code(body.content or "", tid, body.language, cid),
        "table": lambda: pipeline.ingest_table(body.content or "", tid, cid),
    }

    handler = modality_map.get(body.modality)
    if not handler:
        raise HTTPException(400, f"Unsupported modality: {body.modality}")

    job = await handler()

    return {
        "job_id": job.job_id,
        "status": job.status,
        "modality": body.modality,
        "span_count": len(job.spans),
        "spans": [
            {
                "content": s.content[:200],  # Preview
                "modality": s.modality.value,
                "source_page": s.source_page,
                "source_frame": s.source_frame,
                "timestamp_start": s.timestamp_start,
                "confidence": s.confidence,
            }
            for s in job.spans
        ],
        "error": job.error,
        # D-11: surface how this asset was actually embedded so callers never
        # assume a real (pixel/audio-level) multimodal embedding happened
        # when the concrete strategy is caption/transcript-then-text-embed.
        "embedding_strategy": job.metadata.get("embedding_strategy"),
        "real_multimodal_embedding": job.metadata.get("real_multimodal_embedding"),
        # D-14: which router-selected model performed extraction.
        "extractor_model": job.metadata.get("extractor_model"),
    }


@router.get("/jobs/{job_id}")
async def get_job_status(request: Request, job_id: str) -> dict[str, Any]:
    """Get the status of an ingestion job."""
    tenant = _require_tenant(request)
    pipeline = _get_pipeline(request)

    job = await pipeline.get_job(job_id, tenant.tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")

    return {
        "job_id": job.job_id,
        "status": job.status,
        "asset_type": job.asset_type.value,
        "span_count": len(job.spans),
        "error": job.error,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }
