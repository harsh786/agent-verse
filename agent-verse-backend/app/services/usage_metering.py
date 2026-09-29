"""Usage metering hooks shared by the API (in-process) and Celery worker goal paths.

Nothing used to call ``UsageService.record*``, so ``/billing/usage`` was always
empty. Both execution paths now meter from the events they already emit:

* every ``tool_call_complete`` event → one ``tool_calls`` record;
* the goal reaching a terminal status → one ``goals`` record plus its
  ``llm_tokens`` (from the durable per-goal cost breakdown).

Each call flushes immediately — the usage buffer is per process, so anything
left in it is invisible to other replicas and lost when a worker exits.
Metering never breaks a goal, but a failure is logged, never silently dropped.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_CALL_EVENT = "tool_call_complete"
TERMINAL_EVENT_STATUS = {
    "goal_complete": "complete",
    "goal_failed": "failed",
    "goal_cancelled": "cancelled",
}


async def meter_tool_call(
    usage: Any, *, tenant_id: str, goal_id: str | None, event: dict[str, Any]
) -> None:
    """Record one tool call from a ``tool_call_complete`` event."""
    if usage is None:
        return
    try:
        success = event.get("success")
        await usage.record_tool_call(
            tenant_id=tenant_id,
            tool_name=str(event.get("tool") or event.get("tool_name") or "unknown"),
            server_id=str(event.get("server_id") or ""),
            goal_id=goal_id,
            success=bool(success) if success is not None else None,
        )
        await usage.flush()
    except Exception as exc:
        logger.warning(
            "usage_tool_call_record_failed",
            tenant_id=tenant_id,
            goal_id=goal_id,
            error=str(exc)[:200],
        )


async def meter_goal_completion(
    usage: Any, *, tenant_id: str, goal_id: str, status: str
) -> None:
    """Record a goal reaching a terminal status, with its LLM token usage."""
    if usage is None:
        return
    input_tokens = output_tokens = 0
    cost_usd = 0.0
    try:
        from app.observability.cost_breakdown import aget_breakdown

        breakdown = await aget_breakdown(goal_id, tenant_id=tenant_id)
        input_tokens = sum(e.input_tokens for e in breakdown.entries)
        output_tokens = sum(e.output_tokens for e in breakdown.entries)
        cost_usd = float(breakdown.total_cost())
    except Exception as exc:
        # The goal is still counted; only its token attribution is missing.
        logger.warning(
            "usage_goal_tokens_unavailable", goal_id=goal_id, error=str(exc)[:200]
        )
    try:
        await usage.record_goal_completion(
            tenant_id=tenant_id,
            goal_id=goal_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            status=status,
        )
        await usage.flush()
    except Exception as exc:
        logger.warning(
            "usage_goal_completion_record_failed",
            tenant_id=tenant_id,
            goal_id=goal_id,
            error=str(exc)[:200],
        )
