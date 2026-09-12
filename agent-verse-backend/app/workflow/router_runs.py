"""Workflow run detail endpoints.

Endpoints:
  GET    /runs/{run_id}                   Get run detail
  GET    /runs/{run_id}/steps             List all step results
  GET    /runs/{run_id}/steps/{step_id}   Get single step result
  POST   /runs/{run_id}/cancel            Cancel a running workflow
  POST   /runs/{run_id}/pause             Pause a running workflow
  POST   /runs/{run_id}/resume            Resume a paused workflow
  GET    /runs/{run_id}/stream            SSE stream of run events
  POST   /runs/{run_id}/retry             Retry a failed run
  GET    /runs/{run_id}/debug             Debug info (step outputs, vars)
  GET    /runs                            List all runs for tenant (with filters)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.observability.logging import get_logger
from app.workflow.state import WorkflowRunStatus

_log = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["workflow-runs"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class StepResultResponse(BaseModel):
    step_id: str
    step_type: str
    status: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None

    model_config = {"from_attributes": True}


class RunDetailResponse(BaseModel):
    run_id: str
    workflow_id: str
    workflow_name: str | None = None
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    step_count: int = 0
    cost_usd: float = 0.0
    tokens_used: int = 0

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    items: list[RunDetailResponse]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "workflow_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Workflow service not available")
    return svc


def _tenant_id(request: Request) -> Any:
    # TenantMiddleware sets ``request.state.tenant`` per request; prefer it so
    # runs are scoped to the caller's tenant. Fall back to the app-state context
    # (single-tenant / non-middleware wirings). Mirrors router.py::_get_tenant.
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return tenant.tenant_id


# ---------------------------------------------------------------------------
# Run list
# ---------------------------------------------------------------------------


@router.get("", response_model=RunListResponse)
async def list_runs(
    request: Request,
    workflow_id: str | None = Query(None),
    run_status: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> Any:
    """List workflow runs for the current tenant."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    items, total = await svc.list_runs(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        status_filter=run_status,
        page=page,
        per_page=per_page,
    )
    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    item = await svc.get_run(tenant_id=tenant_id, run_id=run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return item


@router.get("/{run_id}/steps", response_model=list[StepResultResponse])
async def list_step_results(run_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await svc.list_step_results(tenant_id=tenant_id, run_id=run_id)


@router.get("/{run_id}/steps/{step_id}", response_model=StepResultResponse)
async def get_step_result(run_id: str, step_id: str, request: Request) -> Any:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    item = await svc.get_step_result(tenant_id=tenant_id, run_id=run_id, step_id=step_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Step result not found")
    return item


# ---------------------------------------------------------------------------
# Run lifecycle control
# ---------------------------------------------------------------------------


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str, request: Request) -> dict[str, str]:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    ok = await svc.cancel_run(tenant_id=tenant_id, run_id=run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Run not found or already terminal")
    return {"run_id": run_id, "status": WorkflowRunStatus.CANCELLED.value}


@router.post("/{run_id}/pause", status_code=status.HTTP_202_ACCEPTED)
async def pause_run(run_id: str, request: Request) -> dict[str, str]:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    ok = await svc.pause_run(tenant_id=tenant_id, run_id=run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Run cannot be paused in current state")
    return {"run_id": run_id, "status": WorkflowRunStatus.PAUSED.value}


@router.post("/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_run(run_id: str, request: Request) -> dict[str, str]:
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    ok = await svc.resume_run(tenant_id=tenant_id, run_id=run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Run is not paused")
    return {"run_id": run_id, "status": WorkflowRunStatus.RUNNING.value}


@router.post("/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(run_id: str, request: Request) -> dict[str, str]:
    """Retry a failed run from the last completed checkpoint."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    new_run_id = await svc.retry_run(tenant_id=tenant_id, run_id=run_id)
    if new_run_id is None:
        raise HTTPException(status_code=409, detail="Run is not in a failed state")
    return {"run_id": new_run_id, "status": WorkflowRunStatus.PENDING.value}


# ---------------------------------------------------------------------------
# Debug endpoint (internal / admin)
# ---------------------------------------------------------------------------


@router.get("/{run_id}/debug")
async def debug_run(run_id: str, request: Request) -> Any:
    """Return full run state including step outputs and variable snapshot.

    This endpoint is rate-limited and should only be called for debugging.
    """
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    debug_data = await svc.get_run_debug(tenant_id=tenant_id, run_id=run_id)
    if debug_data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return debug_data


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@router.get("/{run_id}/stream")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream for a workflow run.

    Emits events:
      data: {"event": "step_started", "step_id": "...", "type": "..."}
      data: {"event": "step_completed", "step_id": "...", "output_keys": [...]}
      data: {"event": "step_failed", "step_id": "...", "error": "..."}
      data: {"event": "run_completed", "status": "success", "outputs": {...}}
      data: {"event": "run_failed", "status": "failed", "error": "..."}
      data: [DONE]
    """
    svc = _svc(request)
    tenant_id = _tenant_id(request)

    async def event_generator() -> AsyncGenerator[str, None]:
        import json as _json

        async for event in svc.stream_run_events(tenant_id=tenant_id, run_id=run_id):
            yield f"data: {_json.dumps(event)}\n\n"
            if event.get("event") in ("run_completed", "run_failed"):
                break
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
