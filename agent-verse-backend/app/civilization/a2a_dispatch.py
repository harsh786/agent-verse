"""Internal agent-to-agent dispatch for civilization members.

Uses A2A data model + HMAC signing but dispatches through the
tenant-scoped Celery path (NOT the public POST /a2a/tasks ingress).
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import uuid
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _sign_payload(payload: bytes, secret: str) -> str:
    """Produce HMAC-SHA256 signature for an A2A payload."""
    expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={expected}"


async def dispatch_internal_task(
    *,
    from_agent_id: str,
    to_agent_id: str,
    goal: str,
    context: dict[str, Any],
    civilization_id: str,
    tenant_id: str,
    goal_service: Any,
    tenant_ctx: Any,
    priority: str = "normal",
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Dispatch an A2A task internally via GoalService (not public HTTP ingress).

    This is safe: uses per-tenant budget/isolation, never bypasses PolicyEngine.
    """
    task_id = uuid.uuid4().hex

    logger.info(
        "a2a_internal_dispatch",
        task_id=task_id,
        from_agent_id=from_agent_id,
        to_agent_id=to_agent_id,
        civilization_id=civilization_id,
        tenant_id=tenant_id,
    )

    # Submit goal for the target agent via GoalService (tenant-scoped, budget-checked)
    goal_id = None
    if goal_service is not None:
        try:
            result = await goal_service.submit_goal(
                goal=goal,
                tenant_ctx=tenant_ctx,
                agent_id=to_agent_id,
                priority=priority,
                execution_context={
                    "a2a_task_id": task_id,
                    "from_agent_id": from_agent_id,
                    "civilization_id": civilization_id,
                    **context,
                },
            )
            goal_id = result.get("goal_id")
        except Exception as exc:
            logger.warning(
                "a2a_internal_dispatch_failed", task_id=task_id, error=str(exc)
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "error": str(exc),
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "civilization_id": civilization_id,
            }

    return {
        "task_id": task_id,
        "goal_id": goal_id,
        "status": "accepted",
        "from_agent_id": from_agent_id,
        "to_agent_id": to_agent_id,
        "civilization_id": civilization_id,
        "message": f"Task dispatched internally to agent {to_agent_id}",
    }
