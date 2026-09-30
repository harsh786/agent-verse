"""Task queue enqueue adapters for goal workers."""

from __future__ import annotations

from typing import Any, Protocol


class GoalTaskQueue(Protocol):
    """Minimal queue interface explicitly injected into GoalService."""

    def enqueue_goal(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        connector_ids: list[str] | None = None,
        workflow_mode: str = "single_agent",
        goal_template: str = "",
        plan: str = "free",
    ) -> str:
        """Enqueue a goal worker task and return the backend task id."""

        ...


class CeleryGoalTaskQueue:
    """Celery-backed enqueue adapter; status bridging is handled separately."""

    def enqueue_goal(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        connector_ids: list[str] | None = None,
        workflow_mode: str = "single_agent",
        goal_template: str = "",
        plan: str = "free",
        trigger_chain_depth: int = 0,
        source_trigger_id: str = "",
    ) -> str:
        from app.scaling.celery_app import PLAN_QUEUE_MAP
        from app.scaling.tasks import run_goal

        target_queue = PLAN_QUEUE_MAP.get(plan, "goals.free")
        # Sent only for chained goals, so workers predating the kwarg keep
        # accepting ordinary goals during a rolling deploy.
        extra: dict[str, Any] = (
            {"trigger_chain_depth": int(trigger_chain_depth)} if trigger_chain_depth else {}
        )
        if source_trigger_id:
            extra["source_trigger_id"] = source_trigger_id
        result: Any = run_goal.apply_async(
            kwargs={
                **extra,
                "goal_id": goal_id,
                "tenant_id": tenant_id,
                "goal_text": goal_text,
                "priority": priority,
                "dry_run": dry_run,
                "agent_id": agent_id or "",
                "connector_ids": connector_ids or [],
                "workflow_mode": workflow_mode,
                "goal_template": goal_template,
                "plan": plan,
            },
            queue=target_queue,
        )
        return str(getattr(result, "id", ""))
