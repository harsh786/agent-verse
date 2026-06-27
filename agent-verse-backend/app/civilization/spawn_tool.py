"""spawn() — the governed tool exposed to agents to create child agents."""
from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

SPAWN_TOOL_DEFINITION = {
    "name": "civilization_spawn",
    "description": (
        "Spawn a new child agent within the civilization to handle a specific "
        "capability or sub-task. "
        "The Governor will enforce Constitution limits (depth, budget, rate). "
        "Returns the new agent's ID if approved, or an error message if denied."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": (
                    "The capability or specialization needed "
                    "(e.g., 'jira_issue_triage', 'confluence_writer')"
                ),
            },
            "goal": {
                "type": "string",
                "description": "The specific goal for the child agent to accomplish",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high"],
                "default": "normal",
            },
        },
        "required": ["capability", "goal"],
    },
}


async def execute_spawn_tool(
    *,
    capability: str,
    goal: str,
    priority: str = "normal",
    # Context injected by the agent runner
    governor: Any,
    requester_agent_id: str,
    depth: int,
    parent_budget_usd: float,
    parent_policy_ids: list[str],
    tenant_ctx: Any,
    goal_service: Any = None,
    civilization_id: str,
) -> dict[str, Any]:
    """Execute the spawn tool. Returns structured result for the LLM."""
    if governor is None:
        return {
            "success": False,
            "error": "Civilization Governor not available — spawn disabled",
        }

    # Get spawn verdict
    verdict = await governor.evaluate_spawn_request(
        requester_agent_id=requester_agent_id,
        requested_capability=capability,
        goal_text=goal,
        depth=depth,
        parent_budget_usd=parent_budget_usd,
        parent_policy_ids=parent_policy_ids,
        tenant_ctx=tenant_ctx,
    )

    from app.civilization.models import SpawnDecision
    if verdict.decision == SpawnDecision.DENIED:
        logger.info(
            "spawn_tool_denied",
            civilization_id=civilization_id,
            capability=capability,
            reason=verdict.reason,
        )
        return {
            "success": False,
            "denied": True,
            "reason": verdict.reason,
            "suggestion": "Proceed without spawning — handle this capability yourself or skip.",
        }

    # Approved — actually spawn
    try:
        agent_record = await governor.spawn_agent(
            verdict=verdict,
            requested_capability=capability,
            goal_text=goal,
            requester_agent_id=requester_agent_id,
            depth=depth,
            tenant_ctx=tenant_ctx,
        )
        agent_id = agent_record.get("agent_id", "unknown")

        # Submit the goal for the new agent
        goal_id = None
        if goal_service is not None:
            try:
                result = await goal_service.submit_goal(
                    goal=goal,
                    tenant_ctx=tenant_ctx,
                    agent_id=agent_id,
                    priority=priority,
                )
                goal_id = result.get("goal_id")
            except Exception as exc:
                logger.warning("spawn_tool_goal_submit_failed", error=str(exc))

        return {
            "success": True,
            "agent_id": agent_id,
            "goal_id": goal_id,
            "capability": capability,
            "budget_usd": verdict.allowed_budget_usd,
            "message": f"Spawned agent '{agent_record.get('name', agent_id)}' for: {goal[:100]}",
        }
    except Exception as exc:
        logger.error("spawn_tool_execution_failed", error=str(exc))
        return {"success": False, "error": str(exc)}
