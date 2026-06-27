"""A2A (Agent-to-Agent) protocol — full implementation with DB persistence, HMAC auth, callbacks."""
from __future__ import annotations

import hashlib
import hmac as _hmac
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["a2a"])

# In-memory fallback (used when DB not available)
_tasks: dict[str, dict[str, Any]] = {}


def _get_a2a_secret() -> str:
    return os.getenv("A2A_SHARED_SECRET", "")


def _verify_hmac(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature of incoming A2A task."""
    if not secret:
        return True  # Disabled in dev
    if not signature:
        return False
    expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return _hmac.compare_digest(f"sha256={expected}", signature)


async def _persist_task(task_id: str, data: dict[str, Any], db: Any) -> None:
    """Write A2A task to DB."""
    if db is None:
        _tasks[task_id] = data
        return
    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(
                text("""INSERT INTO a2a_tasks
                    (id, tenant_id, goal_text, status, callback_url, requester_id, created_at)
                    VALUES (:id, :tid, :goal, :status, :cb, :req, NOW())
                    ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status"""),
                {
                    "id": task_id,
                    "tid": data.get("tenant_id", "a2a-default"),
                    "goal": data.get("goal", ""),
                    "status": data.get("status", "pending"),
                    "cb": data.get("callback_url", ""),
                    "req": data.get("requester_agent_id", ""),
                }
            )
    except Exception as exc:
        logger.warning("a2a_task_persist_failed", error=str(exc))
        _tasks[task_id] = data


async def _update_task_status(task_id: str, status: str, result: str, db: Any) -> None:
    """Update A2A task status in DB."""
    if db is None:
        if task_id in _tasks:
            _tasks[task_id]["status"] = status
            _tasks[task_id]["result"] = result
        return
    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(
                text("UPDATE a2a_tasks SET status=:status, result=:result, updated_at=NOW() WHERE id=:id"),
                {"id": task_id, "status": status, "result": result[:10000] if result else ""}
            )
    except Exception as exc:
        logger.warning("a2a_task_update_failed", error=str(exc))


async def _get_task(task_id: str, db: Any) -> dict[str, Any] | None:
    """Fetch A2A task from DB or in-memory dict."""
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session:
                result = await session.execute(
                    text("SELECT id, goal_text, status, result, callback_url, created_at FROM a2a_tasks WHERE id=:id"),
                    {"id": task_id}
                )
                row = result.fetchone()
            if row:
                return {
                    "task_id": row[0], "goal": row[1], "status": row[2],
                    "result": row[3], "callback_url": row[4],
                    "created_at": row[5].isoformat() if row[5] else ""
                }
        except Exception:
            pass
    return _tasks.get(task_id)


async def _send_callback(callback_url: str, task_id: str, status: str, result: str) -> None:
    """POST task completion to callback URL."""
    if not callback_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json={
                "task_id": task_id,
                "status": status,
                "result": result,
                "completed_at": datetime.now(UTC).isoformat(),
            })
        logger.info("a2a_callback_sent", task_id=task_id, url=callback_url)
    except Exception as exc:
        logger.warning("a2a_callback_failed", task_id=task_id, error=str(exc))


class A2ATaskRequest(BaseModel):
    goal: str
    context: dict[str, Any] = {}
    callback_url: str | None = None
    requester_agent_id: str | None = None
    priority: str = "normal"


@router.get("/.well-known/agent.json")
async def agent_card(request: Request) -> dict[str, Any]:
    """Return this agent's capability card for A2A discovery."""
    return {
        "agent_id": "agentverse-platform",
        "name": "AgentVerse Platform",
        "version": "0.1.0",
        "description": "World-class Agentic OS with goal execution, connectors, and governance",
        "endpoint": str(request.base_url).rstrip("/") + "/a2a",
        "authentication": {
            "scheme": "hmac-sha256",
            "header": "X-A2A-Signature",
            "note": "Set A2A_SHARED_SECRET env var. Empty = disabled (dev mode)."
        },
        "capabilities": [
            "goal_execution", "multi_agent", "rag_search", "connector_tools",
            "hitl_approval", "audit_log", "persistence", "streaming"
        ],
        "supported_task_types": ["goal", "query", "action"],
    }


@router.post("/a2a/tasks", status_code=202)
async def receive_a2a_task(
    request: Request,
    body: A2ATaskRequest,
) -> dict[str, Any]:
    """Receive a task from another agent via A2A protocol."""
    # Verify HMAC signature
    raw_body = await request.body()
    signature = request.headers.get("X-A2A-Signature", "")
    secret = _get_a2a_secret()
    if not _verify_hmac(raw_body, signature, secret):
        raise HTTPException(401, "Invalid A2A signature")

    task_id = uuid.uuid4().hex
    db = getattr(request.app.state, "db_session_factory", None)
    goal_service = getattr(request.app.state, "goal_service", None)

    # Get tenant context for A2A tasks
    from app.tenancy.context import PlanTier, TenantContext
    a2a_tenant_id = os.getenv("A2A_TENANT_ID", "a2a-system")
    tenant_ctx = TenantContext(
        tenant_id=a2a_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id="a2a-inbound",
    )

    task_data = {
        "task_id": task_id,
        "goal": body.goal,
        "status": "accepted",
        "callback_url": body.callback_url or "",
        "requester_agent_id": body.requester_agent_id or "",
        "tenant_id": a2a_tenant_id,
        "created_at": datetime.now(UTC).isoformat(),
    }

    await _persist_task(task_id, task_data, db)

    # Execute goal asynchronously
    if goal_service:
        import asyncio

        async def execute_and_callback() -> None:
            try:
                result = await goal_service.submit_goal(
                    goal=body.goal, priority=body.priority,
                    dry_run=False, tenant_ctx=tenant_ctx,
                )
                goal_id = result["goal_id"]
                final_status = "complete"
                final_result = f"Goal submitted: {goal_id}"

                # Wait for completion
                try:
                    async with asyncio.timeout(300):
                        async for evt in goal_service.subscribe_events(
                            goal_id=goal_id, tenant_ctx=tenant_ctx
                        ):
                            if evt.get("type") == "goal_complete":
                                final_status = "complete"
                                final_result = f"Goal {goal_id} completed"
                                break
                            elif evt.get("type") == "goal_failed":
                                final_status = "failed"
                                final_result = evt.get("reason", "failed")
                                break
                except asyncio.TimeoutError:
                    final_status = "timeout"
                    final_result = "Goal timed out"

            except Exception as exc:
                final_status = "error"
                final_result = str(exc)

            await _update_task_status(task_id, final_status, final_result, db)
            await _send_callback(body.callback_url or "", task_id, final_status, final_result)

        asyncio.create_task(execute_and_callback())

    return {
        "task_id": task_id,
        "status": "accepted",
        "message": f"Task accepted. Track at /a2a/tasks/{task_id}",
    }


@router.get("/a2a/tasks/{task_id}")
async def get_a2a_task(request: Request, task_id: str) -> dict[str, Any]:
    """Get A2A task status and result."""
    db = getattr(request.app.state, "db_session_factory", None)
    task = await _get_task(task_id, db)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    return task
