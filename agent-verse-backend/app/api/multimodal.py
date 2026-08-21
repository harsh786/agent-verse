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
    modality: str  # text | image | pdf | audio | video
    content: str | None = None  # For text
    base64_data: str | None = None  # For binary types
    filename: str | None = None
    collection_id: str | None = None


@router.post("/ingest")
async def ingest_asset(request: Request, body: IngestRequest) -> dict[str, Any]:
    """Ingest a multimodal asset and extract structured spans."""
    tenant = _require_tenant(request)
    from app.multimodal.pipeline import multimodal_pipeline

    provider = getattr(request.app.state, "_app_provider", None)
    if provider:
        multimodal_pipeline.set_provider(provider)

    tid = tenant.tenant_id
    cid = body.collection_id
    data = body.base64_data or ""

    modality_map = {
        "text": lambda: multimodal_pipeline.ingest_text(body.content or "", tid, cid),
        "image": lambda: multimodal_pipeline.ingest_image(data, tid, cid, body.filename),
        "pdf": lambda: multimodal_pipeline.ingest_pdf(data, tid, cid, body.filename),
        "audio": lambda: multimodal_pipeline.ingest_audio(data, tid, cid),
        "video": lambda: multimodal_pipeline.ingest_video(data, tid, cid),
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
    }


@router.get("/jobs/{job_id}")
async def get_job_status(request: Request, job_id: str) -> dict[str, Any]:
    """Get the status of an ingestion job."""
    tenant = _require_tenant(request)
    from app.multimodal.pipeline import multimodal_pipeline

    job = multimodal_pipeline.get_job(job_id, tenant.tenant_id)
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
