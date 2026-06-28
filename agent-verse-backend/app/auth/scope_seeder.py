"""Scope and builtin-role seeder.

Runs once during ``lifespan`` to ensure the canonical scope definitions and
builtin role templates exist in the database.  Uses ``ON CONFLICT DO NOTHING``
so it is safe to run on every startup (idempotent).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Canonical scope definitions (seeded into scope_definitions table)
# ---------------------------------------------------------------------------

BUILTIN_SCOPES: list[dict[str, str]] = [
    {
        "scope": "goals:read",
        "resource": "goals",
        "action": "read",
        "description": "List and retrieve goals",
        "risk_level": "low",
    },
    {
        "scope": "goals:write",
        "resource": "goals",
        "action": "write",
        "description": "Create and update goals",
        "risk_level": "medium",
    },
    {
        "scope": "goals:delete",
        "resource": "goals",
        "action": "delete",
        "description": "Permanently delete goals",
        "risk_level": "high",
    },
    {
        "scope": "goals:execute",
        "resource": "goals",
        "action": "execute",
        "description": "Trigger goal execution",
        "risk_level": "high",
    },
    {
        "scope": "agents:read",
        "resource": "agents",
        "action": "read",
        "description": "List and retrieve agent configs",
        "risk_level": "low",
    },
    {
        "scope": "agents:write",
        "resource": "agents",
        "action": "write",
        "description": "Create and update agent configs",
        "risk_level": "medium",
    },
    {
        "scope": "agents:delete",
        "resource": "agents",
        "action": "delete",
        "description": "Delete agent configurations",
        "risk_level": "high",
    },
    {
        "scope": "knowledge:read",
        "resource": "knowledge",
        "action": "read",
        "description": "Query knowledge bases",
        "risk_level": "low",
    },
    {
        "scope": "knowledge:write",
        "resource": "knowledge",
        "action": "write",
        "description": "Ingest documents into knowledge bases",
        "risk_level": "medium",
    },
    {
        "scope": "knowledge:delete",
        "resource": "knowledge",
        "action": "delete",
        "description": "Remove knowledge base documents",
        "risk_level": "high",
    },
    {
        "scope": "governance:read",
        "resource": "governance",
        "action": "read",
        "description": "View policies and HITL approvals",
        "risk_level": "low",
    },
    {
        "scope": "governance:write",
        "resource": "governance",
        "action": "write",
        "description": "Create and modify governance policies",
        "risk_level": "high",
    },
    {
        "scope": "governance:approve",
        "resource": "governance",
        "action": "approve",
        "description": "Approve HITL requests",
        "risk_level": "critical",
    },
    {
        "scope": "tenancy:read",
        "resource": "tenancy",
        "action": "read",
        "description": "Read tenant settings and configuration",
        "risk_level": "low",
    },
    {
        "scope": "tenancy:write",
        "resource": "tenancy",
        "action": "write",
        "description": "Modify tenant settings",
        "risk_level": "critical",
    },
    {
        "scope": "audit:read",
        "resource": "audit",
        "action": "read",
        "description": "Read audit log entries",
        "risk_level": "medium",
    },
    {
        "scope": "audit:export",
        "resource": "audit",
        "action": "export",
        "description": "Export audit logs to file or SIEM",
        "risk_level": "high",
    },
    {
        "scope": "costs:read",
        "resource": "costs",
        "action": "read",
        "description": "View cost and token usage data",
        "risk_level": "low",
    },
    {
        "scope": "costs:admin",
        "resource": "costs",
        "action": "admin",
        "description": "Set budgets and alert thresholds",
        "risk_level": "high",
    },
    {
        "scope": "mcp:read",
        "resource": "mcp",
        "action": "read",
        "description": "List MCP connectors",
        "risk_level": "low",
    },
    {
        "scope": "mcp:write",
        "resource": "mcp",
        "action": "write",
        "description": "Configure MCP connectors",
        "risk_level": "high",
    },
]

# ---------------------------------------------------------------------------
# Builtin role templates (seeded into custom_roles with tenant_id=NULL)
# ---------------------------------------------------------------------------

BUILTIN_ROLES: list[dict[str, Any]] = [
    {
        "id": "builtin_admin",
        "name": "admin",
        "display_name": "Administrator",
        "description": "Full platform access — all scopes",
        "permissions": [s["scope"] for s in BUILTIN_SCOPES],
        "is_template": True,
        "system_role": "admin",
    },
    {
        "id": "builtin_operator",
        "name": "operator",
        "display_name": "Operator",
        "description": "Operational access: goals, agents, knowledge, connectors",
        "permissions": [
            "goals:read", "goals:write", "goals:execute",
            "agents:read", "agents:write",
            "knowledge:read", "knowledge:write",
            "mcp:read", "mcp:write",
            "tenancy:read",
        ],
        "is_template": True,
        "system_role": "operator",
    },
    {
        "id": "builtin_viewer",
        "name": "viewer",
        "display_name": "Viewer",
        "description": "Read-only access to all resources",
        "permissions": [
            "goals:read",
            "agents:read",
            "knowledge:read",
            "governance:read",
            "tenancy:read",
            "costs:read",
            "audit:read",
            "mcp:read",
        ],
        "is_template": True,
        "system_role": "viewer",
    },
    {
        "id": "builtin_approver",
        "name": "approver",
        "display_name": "Approver",
        "description": "HITL governance approval authority",
        "permissions": [
            "goals:read",
            "governance:read",
            "governance:approve",
            "tenancy:read",
            "audit:read",
        ],
        "is_template": True,
        "system_role": "approver",
    },
    {
        "id": "builtin_agent_service",
        "name": "agent_service",
        "display_name": "Agent Service",
        "description": "Minimal scopes for internal agent-to-agent service calls",
        "permissions": [
            "goals:read", "goals:execute",
            "knowledge:read",
        ],
        "is_template": True,
        "system_role": "agent_service",
    },
]


async def seed_scope_definitions(db: Any) -> None:
    """Upsert canonical scope definitions into the scope_definitions table.

    Safe to call multiple times (idempotent via ON CONFLICT DO NOTHING).
    """
    try:
        from sqlalchemy import text as _t

        for s in BUILTIN_SCOPES:
            await db.execute(
                _t(
                    """
                    INSERT INTO scope_definitions
                        (id, scope, resource, action, description, risk_level)
                    VALUES (gen_random_uuid(), :scope, :resource, :action,
                            :description, :risk_level)
                    ON CONFLICT (scope) DO NOTHING
                    """
                ),
                {
                    "scope": s["scope"],
                    "resource": s["resource"],
                    "action": s["action"],
                    "description": s["description"],
                    "risk_level": s["risk_level"],
                },
            )
        await db.commit()
        logger.info("scope_definitions_seeded", count=len(BUILTIN_SCOPES))
    except Exception as exc:
        logger.warning("scope_definitions_seed_failed", error=str(exc))


async def seed_builtin_scopes(db_factory: Any) -> None:
    """Ensure builtin role templates and scope definitions exist in the DB.

    Creates ``custom_roles`` rows with ``tenant_id=NULL`` for each builtin
    role, and populates the ``scope_definitions`` catalog.

    Idempotent — uses ``ON CONFLICT (id) DO NOTHING``.
    """
    try:
        from sqlalchemy import text as _t

        async with db_factory() as db:
            # Seed scope definitions
            await seed_scope_definitions(db)

            # Seed builtin role templates
            for role in BUILTIN_ROLES:
                import json

                await db.execute(
                    _t(
                        """
                        INSERT INTO custom_roles
                            (id, tenant_id, name, display_name, description,
                             permissions, is_template, system_role, is_active)
                        VALUES (:id, NULL, :name, :display_name, :description,
                                CAST(:permissions AS jsonb), TRUE, :system_role, TRUE)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": role["id"],
                        "name": role["name"],
                        "display_name": role["display_name"],
                        "description": role.get("description", ""),
                        "permissions": json.dumps(role.get("permissions", [])),
                        "system_role": role.get("system_role"),
                    },
                )
            await db.commit()
            logger.info("builtin_roles_seeded", count=len(BUILTIN_ROLES))

    except Exception as exc:
        # Non-fatal: the platform degrades gracefully if DB is unavailable at boot
        logger.warning("builtin_roles_seed_failed", error=str(exc))
