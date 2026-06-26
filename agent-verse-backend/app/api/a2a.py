"""Agent-to-Agent (A2A) protocol endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.mcp.a2a import A2ATask, A2ATaskResult, AgentCard

router = APIRouter(tags=["a2a"])

# In-memory task store for received tasks
_received_tasks: dict[str, A2ATask] = {}
_task_results: dict[str, A2ATaskResult] = {}


@router.get("/.well-known/agent.json")
async def get_agent_card(request: Request) -> dict[str, Any]:
    """Return the agent card for this AgentVerse instance."""
    base_url = str(request.base_url).rstrip("/")
    card = AgentCard(
        agent_id="agentverse-platform",
        name="AgentVerse Platform",
        description="Multi-tenant autonomous agent operating system",
        version="0.1.0",
        capabilities=["goals", "planning", "tool-use", "rag", "governance"],
        endpoint=base_url,
        auth_required=True,
    )
    return card.model_dump()


class SendTaskRequest(BaseModel):
    task_id: str
    goal: str
    context: dict[str, Any] = {}
    callback_url: str | None = None


@router.post("/a2a/tasks")
async def receive_task(request: Request, body: SendTaskRequest) -> dict[str, Any]:
    """Receive a task from another agent and execute it via the agent loop."""
    task = A2ATask(
        task_id=body.task_id,
        goal=body.goal,
        context=body.context,
        callback_url=body.callback_url,
    )
    _received_tasks[task.task_id] = task

    # Execute the goal via GoalService if both the service and tenant context
    # are available (they always are in the running app; may be absent in tests
    # that deliberately bypass middleware).
    goal_svc = getattr(request.app.state, "goal_service", None)
    tenant_ctx = getattr(request.state, "tenant", None)

    if goal_svc is not None and tenant_ctx is not None:
        try:
            result = await goal_svc.submit_goal(
                goal=body.goal,
                priority="normal",
                dry_run=False,
                tenant_ctx=tenant_ctx,
            )
            task.status = "executing"
            task.context["internal_goal_id"] = result.get("goal_id", "")
        except Exception as exc:
            task.status = "failed"
            task.context["error"] = str(exc)
    # else: keep default status="pending" for backward compatibility

    return {"task_id": task.task_id, "status": "received"}


@router.get("/a2a/tasks/{task_id}")
async def get_task_result(task_id: str) -> dict[str, Any]:
    """Get the result of a task sent to this agent."""
    if task_id in _task_results:
        return _task_results[task_id].model_dump()
    if task_id in _received_tasks:
        task = _received_tasks[task_id]
        # Map internal states to external-facing statuses:
        # "executing"/"failed" show real status; others show "pending" (backward compat)
        external_status = task.status if task.status in {"executing", "failed"} else "pending"
        return {"task_id": task_id, "status": external_status}
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
