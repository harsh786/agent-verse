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
        subgoal: bool = False,
    ) -> str:
        """Enqueue a goal worker task and return the backend task id.

        ``subgoal`` marks a supervisor's sub-goal: it is routed to the dedicated
        sub-goal queue family so a parent holding a worker slot never starves it.
        """

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
        subgoal: bool = False,
    ) -> str:
        from app.scaling.celery_app import goal_queue_for
        from app.scaling.tasks import run_goal

        # Supervisor sub-goals go to goals.subgoals.{plan}, consumed only by the
        # dedicated sub-goal pool (CORE-09); everything else to goals.{plan}.
        target_queue = goal_queue_for(plan, subgoal=subgoal)
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
