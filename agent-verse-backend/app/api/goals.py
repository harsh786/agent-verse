"""Goals API router — submit and track autonomous agent goals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.errors import NotFoundError

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    priority: str = "normal"
    dry_run: bool = False


def _goal_service(request: Request) -> Any:
    return request.app.state.goal_service


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_goal(request: Request, body: GoalRequest) -> dict[str, Any]:
    _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.submit_goal(
        goal=body.goal,
        priority=body.priority,
        dry_run=body.dry_run,
    )
    return result


@router.get("/{goal_id}")
async def get_goal(request: Request, goal_id: str) -> dict[str, Any]:
    _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.get_goal(goal_id=goal_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/cancel")
async def cancel_goal(request: Request, goal_id: str) -> dict[str, Any]:
    _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.cancel_goal(goal_id=goal_id)
    return result
