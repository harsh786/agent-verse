"""
Custom Role System v2
======================
Tenants can define their own roles with custom scope combinations.
Built on top of the existing RBAC — extends without replacing.

Pre-defined role templates:
- viewer:    read-only access
- operator:  read + run goals + use agents
- builder:   operator + create/edit agents and templates
- admin:     builder + manage connectors, users, policies
- owner:     full access + billing

Custom role example:
  name: "data-entry"
  inherits: "viewer"
  extra_scopes: ["goals:write", "templates:read"]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Base role definitions (scope sets)
_BASE_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset({
        "goals:read", "agents:read", "templates:read",
        "knowledge:read", "memory:read", "costs:read",
    }),
    "operator": frozenset({
        "goals:read", "goals:write", "goals:stream",
        "agents:read", "agents:run",
        "templates:read", "templates:use",
        "knowledge:read", "memory:read", "costs:read",
        "tools:read", "tools:use",
    }),
    "builder": frozenset({
        "goals:read", "goals:write", "goals:stream",
        "agents:read", "agents:write", "agents:run",
        "templates:read", "templates:write", "templates:use",
        "knowledge:read", "knowledge:write",
        "memory:read", "memory:write",
        "costs:read", "tools:read", "tools:use",
        "skills:read", "skills:write",
        "workflows:read", "workflows:write",
    }),
    "admin": frozenset({
        "goals:read", "goals:write", "goals:stream", "goals:admin",
        "agents:read", "agents:write", "agents:run", "agents:admin",
        "templates:read", "templates:write", "templates:use", "templates:admin",
        "knowledge:read", "knowledge:write", "knowledge:admin",
        "memory:read", "memory:write",
        "costs:read", "costs:admin",
        "tools:read", "tools:use", "tools:admin",
        "connectors:read", "connectors:write",
        "users:read", "users:invite",
        "policies:read", "policies:write",
        "skills:read", "skills:write", "skills:admin",
        "workflows:read", "workflows:write", "workflows:admin",
        "audit:read",
    }),
    "owner": frozenset({
        "*",  # wildcard — all scopes
    }),
}


@dataclass
class CustomRole:
    """A tenant-defined role with custom scope combination."""

    id: str
    tenant_id: str
    name: str
    inherits: str | None  # base role to inherit from
    extra_scopes: list[str] = field(default_factory=list)
    denied_scopes: list[str] = field(default_factory=list)
    description: str = ""
    created_by: str = ""

    def effective_scopes(self) -> frozenset[str]:
        """Compute effective scope set (inherited + extra - denied)."""
        base = _BASE_ROLE_SCOPES.get(self.inherits or "viewer", frozenset())
        extra = frozenset(self.extra_scopes)
        denied = frozenset(self.denied_scopes)
        return (base | extra) - denied


class CustomRoleStore:
    """Per-tenant custom role definitions."""

    def __init__(self) -> None:
        self._roles: dict[str, dict[str, CustomRole]] = {}  # tenant → {name → role}

    def define(self, role: CustomRole) -> None:
        if role.inherits and role.inherits not in _BASE_ROLE_SCOPES:
            raise ValueError(f"Unknown base role: {role.inherits}")
        self._roles.setdefault(role.tenant_id, {})[role.name] = role
        logger.info("custom_role_defined", tenant=role.tenant_id, name=role.name)

    def get(self, tenant_id: str, role_name: str) -> CustomRole | None:
        return self._roles.get(tenant_id, {}).get(role_name)

    def list_for_tenant(self, tenant_id: str) -> list[CustomRole]:
        return list(self._roles.get(tenant_id, {}).values())

    def resolve_scopes(self, tenant_id: str, role_name: str) -> frozenset[str]:
        """Resolve scopes for a role name — built-in or custom."""
        if role_name in _BASE_ROLE_SCOPES:
            return _BASE_ROLE_SCOPES[role_name]
        custom = self.get(tenant_id, role_name)
        if custom:
            return custom.effective_scopes()
        return frozenset()  # unknown role — no scopes (fail-closed)

    def has_scope(self, tenant_id: str, role_name: str, scope: str) -> bool:
        """Check if a role has a specific scope."""
        scopes = self.resolve_scopes(tenant_id, role_name)
        return "*" in scopes or scope in scopes


# Module singleton
_role_store = CustomRoleStore()
