"""Goals API router — submit and track autonomous agent goals."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.core.errors import NotFoundError
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/goals", tags=["goals"])


class PersistenceConfigRequest(BaseModel):
    max_attempts: int = Field(default=10, ge=1, le=100)
    iterations_per_attempt: int = Field(default=15, ge=1, le=50)
    base_backoff_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    max_backoff_seconds: float = Field(default=600.0, ge=10.0, le=86400.0)
    strategy_switch_after: int = Field(default=2, ge=1, le=10)
    escalate_after_failures: int = Field(default=6, ge=1, le=50)
    total_timeout_seconds: float = Field(default=0.0, ge=0.0)
    decompose_on_failure: bool = True


class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
    # Persistence mode: keep trying until goal is achieved
    persistence_mode: bool = False
    persistence_config: PersistenceConfigRequest = Field(
        default_factory=PersistenceConfigRequest
    )


class ApproveRequest(BaseModel):
    request_id: str
    action: str  # "approve" | "reject"
    approver: str
    note: str = ""


def _goal_service(request: Request) -> Any:
    return request.app.state.goal_service


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_goal(request: Request, body: GoalRequest) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)

    # Build execution_context with persistence settings when enabled
    exec_ctx: dict[str, Any] = {}
    if body.persistence_mode:
        exec_ctx["persistence_mode"] = True
        exec_ctx["persistence_config"] = body.persistence_config.model_dump()

    # ── Debate mode: run multi-agent consensus before goal execution ──────────
    if body.workflow_mode == "debate":
        provider = getattr(request.app.state, "_app_provider", None)
        if provider is not None:
            try:
                from app.agent.debate import DebateOrchestrator
                rounds = int(exec_ctx.get("debate_rounds", 2))
                orchestrator = DebateOrchestrator(provider=provider, rounds=rounds)
                debate_result = await orchestrator.run(goal=body.goal)
                exec_ctx["debate_consensus"] = debate_result.winning_proposal
                exec_ctx["debate_confidence"] = debate_result.consensus_level
                exec_ctx["debate_winning_agent"] = debate_result.winning_agent
            except Exception:
                pass  # Fall back to normal execution if debate fails

    result: dict[str, Any] = await svc.submit_goal(
        goal=body.goal,
        priority=body.priority,
        dry_run=body.dry_run,
        tenant_ctx=tenant,
        agent_id=body.agent_id,
        workflow_mode=body.workflow_mode,
        execution_context=exec_ctx,
    )
    return result


@router.get("")
async def list_goals(request: Request) -> dict[str, list[dict[str, Any]]]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, list[dict[str, Any]]] = await svc.list_goals(tenant_ctx=tenant)
    return result


@router.get("/metrics")
async def get_goal_metrics(request: Request) -> dict[str, Any]:
    """Return aggregated metrics for the authenticated tenant's goals."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.get_metrics(tenant_ctx=tenant)
    return result


@router.get("/cost-metrics")
async def get_cost_metrics(request: Request) -> dict[str, Any]:
    """Return cost metrics for dashboard chart."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    metrics = await svc.get_metrics(tenant_ctx=tenant)
    # Get budget config
    budget_configs = getattr(request.app.state, "_budget_config", {})
    from app.governance.cost import BudgetConfig
    budget_cfg: BudgetConfig = budget_configs.get(tenant.tenant_id, BudgetConfig())
    return {
        **metrics,
        "daily_budget_usd": budget_cfg.per_tenant_daily_usd,
        "per_goal_budget_usd": budget_cfg.per_goal_usd,
        "budget_utilization": (
            metrics["cost_today_usd"] / budget_cfg.per_tenant_daily_usd
            if budget_cfg.per_tenant_daily_usd > 0 else 0.0
        ),
    }


@router.get("/{goal_id}")
async def get_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/cancel")
async def cancel_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.cancel_goal(goal_id=goal_id, tenant_ctx=tenant)
    return result


@router.get(
    "/{goal_id}/stream",
    responses={
        200: {
            "description": "Server-Sent Events stream of goal execution lifecycle events.",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "example": (
                            'data: {"type": "goal_started", "goal": "..."}\n\n'
                            'data: {"type": "plan_ready", "steps": ["..."]}\n\n'
                            'data: {"type": "step_started", "step": "..."}\n\n'
                            'data: {"type": "step_complete", "step": "...", "output": "..."}\n\n'
                            'data: {"type": "verification_done", "success": true}\n\n'
                            'data: {"type": "goal_complete"}\n\n'
                        ),
                    }
                }
            },
        },
        401: {"description": "Missing or invalid API key."},
        404: {"description": "Goal not found."},
    },
)
async def stream_goal(request: Request, goal_id: str) -> StreamingResponse:
    """Stream goal execution events as Server-Sent Events (SSE).

    Returns a continuous `text/event-stream` of JSON events for the given goal.
    Each event is a JSON object on a `data:` line followed by two newlines.

    **Event types emitted:**
    - `goal_started` — goal accepted, execution beginning
    - `plan_ready` — planner produced a step list
    - `step_started` — a single step is being executed
    - `step_complete` — step finished with output
    - `waiting_approval` — supervised mode: HITL approval required
    - `approval_granted` — HITL approved, execution resuming
    - `sub_goals_complete` — goal-tree sub-agents finished
    - `verification_done` — verifier evaluated the run
    - `goal_complete` — goal reached `complete` status
    - `goal_failed` — goal reached `failed` status
    - `goal_cancelled` — goal was cancelled via the cancel endpoint
    - `replanning` — verifier returned false; planner will retry

    Connect with `EventSource` (browser) or `httpx` streaming (server-side).
    The stream ends when the goal reaches a terminal status
    (`complete`, `failed`, or `cancelled`).
    """
    _require_tenant(request)
    svc = _goal_service(request)

    async def event_generator() -> AsyncGenerator[str, None]:
        async for event in svc.subscribe_events(goal_id=goal_id, tenant_ctx=request.state.tenant):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # Disable nginx buffering for SSE
            "Connection": "keep-alive",
        },
    )


@router.get("/{goal_id}/audit")
async def get_audit_log(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return audit log entries for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        entries: list[dict[str, Any]] = await svc.get_audit_entries(
            goal_id=goal_id, tenant_ctx=tenant
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return entries


@router.get("/{goal_id}/eval")
async def get_goal_eval(request: Request, goal_id: str) -> dict[str, Any]:
    """Return the eval scorecard for a completed goal, or a not-evaluated response."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.get_eval(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/approve")
async def approve_goal(
    request: Request, goal_id: str, body: ApproveRequest
) -> dict[str, Any]:
    """Approve or reject a pending HITL request for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.handle_approval(
            goal_id=goal_id,
            request_id=body.request_id,
            action=body.action,
            approver=body.approver,
            note=body.note,
            tenant_ctx=tenant,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/pause")
async def pause_goal(request: Request, goal_id: str) -> dict[str, Any]:
    """Pause a running goal. Can be resumed with POST /goals/{id}/resume."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.pause_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/resume")
async def resume_goal(request: Request, goal_id: str) -> dict[str, Any]:
    """Resume a paused goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.resume_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result


class BatchGoalRequest(BaseModel):
    goals: list[str] = Field(..., min_length=1, max_length=100)
    priority: str = "normal"
    agent_id: str | None = None
    max_parallel: int = Field(default=10, ge=1, le=50)


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
async def submit_batch_goals(request: Request, body: BatchGoalRequest) -> dict[str, Any]:
    """Submit multiple goals as a batch for parallel processing."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)

    batch_id = uuid.uuid4().hex
    submitted = []

    for goal_text in body.goals:
        try:
            result = await svc.submit_goal(
                goal=goal_text, priority=body.priority, dry_run=False,
                tenant_ctx=tenant, agent_id=body.agent_id,
            )
            submitted.append({
                "goal_id": result.get("goal_id"),
                "goal": goal_text[:100],
                "status": "queued",
            })
        except Exception as exc:
            submitted.append({
                "goal_id": None,
                "goal": goal_text[:100],
                "status": "error",
                "error": str(exc),
            })

    return {
        "batch_id": batch_id,
        "total": len(body.goals),
        "queued": sum(1 for s in submitted if s["status"] == "queued"),
        "errors": sum(1 for s in submitted if s["status"] == "error"),
        "goals": submitted,
    }


@router.get("/batch/{batch_id}/status")
async def get_batch_status(request: Request, batch_id: str) -> dict[str, Any]:
    """Get status summary for a batch submission (track individual goal_ids for details)."""
    _require_tenant(request)
    return {
        "batch_id": batch_id,
        "message": "Track individual goal_ids from batch submission",
    }


@router.get("/{goal_id}/traces")
async def get_goal_traces(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return decision trace records for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    # Verify goal exists and belongs to tenant
    try:
        await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Query DB for traces
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        # Fall back to in-memory context
        return []
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            result = await session.execute(
                text(
                    """SELECT id, action, reasoning, confidence, created_at
                        FROM decision_traces
                        WHERE goal_id = :gid AND tenant_id = :tid
                        ORDER BY created_at"""
                ),
                {"gid": goal_id, "tid": tenant.tenant_id},
            )
            rows = result.fetchall()
        return [
            {
                "trace_id": r[0],
                "action": r[1],
                "reasoning": r[2],
                "confidence": float(r[3]) if r[3] else 0.5,
                "at": r[4].isoformat() if r[4] else "",
            }
            for r in rows
        ]
    except Exception:
        return []
