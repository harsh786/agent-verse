"""Tenant-scoped Magentic ledger reads and human review."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-magentic"])


class HumanReviewRequest(BaseModel):
    token: str = Field(min_length=16)
    approved: bool
    safe_note: str = Field(default="", max_length=2_000)


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


def _ledger(request: Request) -> Any:
    repository = getattr(request.app.state, "progress_ledger_repository", None)
    if repository is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Ledger unavailable")
    return repository


@router.get("/{session_id}/ledger", operation_id="get_magentic_ledger")
async def get_ledger(request: Request, session_id: str) -> dict[str, Any]:
    revision = await _ledger(request).current(_tenant(request), session_id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger not found")
    return cast(dict[str, Any], revision.model_dump(mode="json"))


@router.get("/{session_id}/ledger/revisions", operation_id="list_magentic_ledger_revisions")
async def list_revisions(
    request: Request,
    session_id: str,
    after_version: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    revisions = await _ledger(request).revisions(
        _tenant(request),
        session_id,
        after_version=after_version,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in revisions],
        "next_version": revisions[-1].version if revisions else after_version,
        "has_more": len(revisions) == limit,
    }


@router.post("/{session_id}/magentic/human-review", operation_id="submit_magentic_human_review")
async def submit_human_review(
    request: Request, session_id: str, body: HumanReviewRequest
) -> dict[str, Any]:
    service = getattr(request.app.state, "magentic_human_review", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Human review unavailable")
    try:
        decision = await service.submit(
            _tenant(request),
            session_id,
            token=body.token,
            approved=body.approved,
            safe_note=body.safe_note,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {
        "session_id": decision.session_id,
        "approved": decision.approved,
        "safe_note": decision.safe_note,
    }


__all__ = ["router"]
