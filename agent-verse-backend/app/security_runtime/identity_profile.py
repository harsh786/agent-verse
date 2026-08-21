"""IdentityProfile — tenant/agent/delegated identity resolution (spec §Layer 1).

Resolves tenant identity, agent identity, delegated permissions, allowed scopes.
Used to determine what actions an agent is permitted to take on behalf of a tenant.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


class IdentityScope(str, enum.Enum):
    TENANT = "tenant"
    AGENT = "agent"
    DELEGATED_AGENT = "delegated_agent"


@dataclass
class IdentityProfile:
    """Resolved identity for a single request/goal execution."""

    tenant_id: str
    identity_scope: IdentityScope
    agent_id: str | None = None
    delegated_permissions: list[str] = field(default_factory=list)
    sponsor_tenant_id: str | None = None  # for delegated/3P agents
    api_key_id: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "identity_scope": self.identity_scope.value,
            "agent_id": self.agent_id,
            "delegated_permissions": list(self.delegated_permissions),
            "sponsor_tenant_id": self.sponsor_tenant_id,
            "roles": list(self.roles),
        }

    def is_delegated(self) -> bool:
        return self.identity_scope == IdentityScope.DELEGATED_AGENT

    def has_permission(self, permission: str) -> bool:
        return permission in self.delegated_permissions or "admin" in self.roles


class IdentityResolver:
    """Resolves IdentityProfile from TenantContext and optional agent_id."""

    def resolve(
        self,
        *,
        tenant_ctx: TenantContext,
        agent_id: str | None = None,
        sponsor_tenant_id: str | None = None,
    ) -> IdentityProfile:
        if sponsor_tenant_id and agent_id:
            scope = IdentityScope.DELEGATED_AGENT
        elif agent_id:
            scope = IdentityScope.AGENT
        else:
            scope = IdentityScope.TENANT

        return IdentityProfile(
            tenant_id=tenant_ctx.tenant_id,
            identity_scope=scope,
            agent_id=agent_id,
            delegated_permissions=[],
            sponsor_tenant_id=sponsor_tenant_id,
            api_key_id=tenant_ctx.api_key_id,
            roles=tenant_ctx.roles,
        )
