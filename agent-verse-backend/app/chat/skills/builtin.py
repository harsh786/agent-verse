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


def register_builtin_skills(registry: SkillRegistry, *, services_api: Any | None = None) -> None:
    """Register the built-in skills whose backing services are available."""
    if services_api is not None:
        registry.register(build_list_connected_services_skill(services_api))
