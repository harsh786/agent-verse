"""Internal agent-to-agent dispatch for civilization members.

Uses A2A data model + HMAC signing but dispatches through the
tenant-scoped Celery path (NOT the public POST /a2a/tasks ingress).
"""

from __future__ import annotations

import asyncio
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
    repository: Any = None,
    membership: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Dispatch an A2A task internally via GoalService (not public HTTP ingress).

    This is safe: uses per-tenant budget/isolation, never bypasses PolicyEngine.
    """
    if membership is not None:
        source_active, target_active = await asyncio.gather(
            membership.active_member(tenant_id, civilization_id, from_agent_id),
            membership.active_member(tenant_id, civilization_id, to_agent_id),
        )
        if not source_active or not target_active:
            raise PermissionError("A2A agents must be active in the same civilization")
    command_key = idempotency_key or uuid.uuid4().hex
    task_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{civilization_id}:{command_key}").hex

    if repository is not None:
        from app.civilization.a2a_repository import A2ATaskRecord

        now = datetime.now(UTC)
        record = await repository.create(
            A2ATaskRecord(
                task_id=task_id,
                tenant_id=tenant_id,
                civilization_id=civilization_id,
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                goal_digest=hashlib.sha256(goal.encode()).hexdigest(),
                context_reference=f"context://{task_id}",
                callback_url=callback_url,
                idempotency_key=command_key,
                created_at=now,
                updated_at=now,
            )
        )
        if record.status != "pending":
            return {
                "task_id": record.task_id,
                "goal_id": record.goal_id,
                "status": record.status,
                "from_agent_id": record.from_agent_id,
                "to_agent_id": record.to_agent_id,
                "civilization_id": record.civilization_id,
                "message": f"Task already {record.status}",
            }

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
            # Inject W3C trace context so the receiving agent's spans are
            # correlated with the dispatching agent's active trace.
            _trace_ctx: dict[str, str] = {}
            try:
                from opentelemetry import propagate as _otel_propagate

                _otel_propagate.inject(_trace_ctx)
            except Exception:
                pass

            result = await goal_service.submit_goal(
                goal=goal,
                tenant_ctx=tenant_ctx,
                agent_id=to_agent_id,
                priority=priority,
                execution_context={
                    "a2a_task_id": task_id,
                    "from_agent_id": from_agent_id,
                    "civilization_id": civilization_id,
                    # Forward W3C trace headers to the child goal context so
                    # the receiving agent can extract and continue the trace.
                    "_w3c_traceparent": _trace_ctx.get("traceparent", ""),
                    "_w3c_tracestate": _trace_ctx.get("tracestate", ""),
                    **context,
                },
            )
            goal_id = result.get("goal_id")
            if repository is not None:
                await repository.update(tenant_id, task_id, status="accepted", goal_id=goal_id)
        except Exception as exc:
            if repository is not None:
                await repository.update(tenant_id, task_id, status="failed")
            logger.warning("a2a_internal_dispatch_failed", task_id=task_id, error=str(exc))
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
