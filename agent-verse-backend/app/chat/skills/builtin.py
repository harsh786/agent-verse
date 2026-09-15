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


def build_generate_document_skill(artifact_store: Any) -> ChatSkill:
    async def handler(
        tenant_id: str, content: str, fmt: str = "pdf", filename: str | None = None
    ) -> dict[str, Any]:
        from app.chat.documents import generate_document, mime_for

        data = generate_document(content, fmt)
        name = filename or f"document.{fmt.lower()}"
        artifact_id = artifact_store.put(
            tenant_id=tenant_id, content=data, mime=mime_for(fmt), filename=name
        )
        return {
            "artifact_id": artifact_id,
            "filename": name,
            "mime": mime_for(fmt),
            "download_url": f"/chat/artifacts/{artifact_id}/download",
        }

    return ChatSkill(
        name="generate_document",
        description="Generate a downloadable document (pdf/md/csv/txt/json) from content.",
        handler=handler,
        args={
            "content": "the document body",
            "fmt": "pdf|md|csv|txt|json",
            "filename": "optional filename",
        },
        scope="documents:write",
    )


def build_list_pending_approvals_skill(hitl_gateway: Any) -> ChatSkill:
    async def handler(tenant_ctx: Any, goal_id: str | None = None) -> list[dict[str, Any]]:
        reqs = hitl_gateway.list_pending(tenant_ctx=tenant_ctx, goal_id=goal_id)
        return [
            {
                "request_id": getattr(r, "request_id", None) or getattr(r, "id", None),
                "goal_id": getattr(r, "goal_id", None),
                "action": getattr(r, "action", None) or getattr(r, "step", None),
                "risk": getattr(r, "risk_level", None),
                "status": str(getattr(r, "status", "")),
            }
            for r in reqs
        ]

    return ChatSkill(
        name="list_pending_approvals",
        description="List pending human-approval (HITL) requests awaiting a decision.",
        handler=handler,
        args={"goal_id": "optional goal id to filter by"},
        scope="governance:read",
    )


def register_builtin_skills(
    registry: SkillRegistry,
    *,
    services_api: Any | None = None,
    goal_service: Any | None = None,
    schedule_store: Any | None = None,
    artifact_store: Any | None = None,
    hitl_gateway: Any | None = None,
) -> None:
    """Register the built-in skills whose backing services are available."""
    if services_api is not None:
        registry.register(build_list_connected_services_skill(services_api))
    if goal_service is not None:
        registry.register(build_submit_goal_skill(goal_service))
    if schedule_store is not None:
        registry.register(build_list_schedules_skill(schedule_store))
    if artifact_store is not None:
        registry.register(build_generate_document_skill(artifact_store))
    if hitl_gateway is not None:
        registry.register(build_list_pending_approvals_skill(hitl_gateway))


def build_registry_from_app_state(app_state: Any) -> SkillRegistry:
    """Assemble a SkillRegistry from whatever services are wired on app.state.

    Unwraps a Starlette app to its .state, then registers each built-in skill
    whose backing service is present. Missing services are simply skipped.
    """
    aps: Any = app_state
    try:
        from starlette.applications import Starlette

        if isinstance(aps, Starlette):
            aps = aps.state
    except Exception:
        pass
    registry = SkillRegistry()
    register_builtin_skills(
        registry,
        services_api=getattr(aps, "services_api", None),
        goal_service=getattr(aps, "goal_service", None),
        schedule_store=getattr(aps, "schedule_store", None),
        artifact_store=getattr(aps, "chat_artifact_store", None),
        hitl_gateway=getattr(aps, "hitl_gateway", None),
    )
    return registry
