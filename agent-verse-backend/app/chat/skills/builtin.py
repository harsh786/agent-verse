"""Built-in chat skills — thin adapters over existing platform services (Phase 5).

Each builder closes over a real service and returns a ChatSkill. register_builtin_
skills wires the ones whose services are available onto a registry. No new business
logic lives here — skills just translate an NL command into an existing call.
"""

from __future__ import annotations

from typing import Any

from app.chat.skills.registry import ChatSkill, SkillRegistry


def build_list_connected_services_skill(services_api: Any) -> ChatSkill:
    async def handler(tenant_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": getattr(s, "id", None),
                "name": getattr(s, "name", None),
                "status": getattr(s, "status", None),
            }
            for s in services_api.list_services(tenant_id)
        ]

    return ChatSkill(
        name="list_connected_services",
        description="List the external services/connectors this tenant has connected.",
        handler=handler,
        args={"tenant_id": "the tenant id"},
        scope="connectors:read",
    )


def build_submit_goal_skill(goal_service: Any) -> ChatSkill:
    async def handler(
        tenant_ctx: Any, goal: str, agent_id: str | None = None
    ) -> dict[str, Any]:
        return await goal_service.submit_goal(
            goal=goal,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
        )

    return ChatSkill(
        name="submit_goal",
        description="Run an autonomous goal (plan→execute→verify with tools/connectors).",
        handler=handler,
        args={"goal": "what to accomplish", "agent_id": "optional agent to route to"},
        scope="goals:write",
    )


def build_list_schedules_skill(schedule_store: Any) -> ChatSkill:
    async def handler(tenant_ctx: Any) -> list[dict[str, Any]]:
        return list(schedule_store.list_all(tenant_ctx=tenant_ctx))

    return ChatSkill(
        name="list_schedules",
        description="List the tenant's recurring/scheduled triggers.",
        handler=handler,
        args={},
        scope="triggers:read",
    )


def register_builtin_skills(
    registry: SkillRegistry,
    *,
    services_api: Any | None = None,
    goal_service: Any | None = None,
    schedule_store: Any | None = None,
) -> None:
    """Register the built-in skills whose backing services are available."""
    if services_api is not None:
        registry.register(build_list_connected_services_skill(services_api))
    if goal_service is not None:
        registry.register(build_submit_goal_skill(goal_service))
    if schedule_store is not None:
        registry.register(build_list_schedules_skill(schedule_store))
