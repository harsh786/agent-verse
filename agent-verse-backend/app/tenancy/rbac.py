"""Role-Based Access Control helpers for AgentVerse.

Roles (most privileged first):
  admin    — full platform control (emergency stop, all writes, user management)
  operator — create/delete agents, run goals, manage connectors
  approver — approve/reject HITL requests
  viewer   — read-only access to goals, audit, agents

Role hierarchy: admin > operator/approver > viewer
"""
from __future__ import annotations

import ipaddress
from typing import Any, Callable

from fastapi import HTTPException, Request, status

from app.tenancy.context import TenantContext

# Role name constants — use these instead of bare strings
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_APPROVER = "approver"
ROLE_VIEWER = "viewer"

# All valid roles
VALID_ROLES = frozenset({"admin", "operator", "viewer", "approver"})

# Role hierarchy: role → set of roles it implies
_ROLE_IMPLIES: dict[str, frozenset[str]] = {
    "admin": frozenset({"admin", "operator", "viewer", "approver"}),
    "operator": frozenset({"operator", "viewer"}),
    "approver": frozenset({"approver", "viewer"}),
    "viewer": frozenset({"viewer"}),
}


def effective_roles(ctx: TenantContext) -> frozenset[str]:
    """Expand ctx.roles with implied roles from hierarchy."""
    result: set[str] = set()
    for role in ctx.roles:
        result.update(_ROLE_IMPLIES.get(role, frozenset({role})))
    return frozenset(result)


def has_role(ctx: TenantContext, role: str) -> bool:
    """Return True if ctx has the given role (with hierarchy expansion)."""
    return role in effective_roles(ctx)


def has_any_role(ctx: TenantContext, roles: list[str]) -> bool:
    """Return True if ctx has any of the given roles."""
    effective = effective_roles(ctx)
    return any(r in effective for r in roles)


def require_role(*roles: str) -> Callable:
    """FastAPI dependency factory that enforces role requirement.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            request: Request,
            _: None = Depends(require_role("admin")),
        ):
            ...
    """
    def dependency(request: Request) -> None:
        ctx: TenantContext | None = getattr(request.state, "tenant", None)
        if ctx is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if not has_any_role(ctx, list(roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Insufficient permissions. Required: one of {list(roles)}. "
                    f"Your roles: {list(ctx.roles)}"
                ),
            )
    return dependency


async def load_roles_from_db(
    user_id: str,
    tenant_id: str,
    db_session_factory: Any,
) -> tuple[str, ...]:
    """Load roles for a user from the user_roles table.

    Returns empty tuple if DB unavailable or user has no roles.
    """
    if db_session_factory is None:
        return ()
    try:
        from sqlalchemy import select

        from app.db.models.rbac import UserRole
        async with db_session_factory() as session:
            result = await session.execute(
                select(UserRole.role).where(
                    UserRole.tenant_id == tenant_id,
                    UserRole.user_id == user_id,
                )
            )
            rows = result.scalars().all()
        return tuple(rows)
    except Exception:
        return ()


def extract_roles_from_jwt(jwt_payload: dict[str, Any]) -> tuple[str, ...]:
    """Extract AgentVerse roles from a Keycloak JWT payload.

    Looks for roles in:
    1. realm_access.roles (Keycloak standard)
    2. resource_access.agentverse.roles (client-specific)
    3. Top-level "roles" claim (custom mapper)

    Only returns roles that are in VALID_ROLES.
    """
    roles: set[str] = set()

    realm_access = jwt_payload.get("realm_access", {})
    for r in realm_access.get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    resource_access = jwt_payload.get("resource_access", {})
    for r in resource_access.get("agentverse", {}).get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    for r in jwt_payload.get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    return tuple(roles)


def is_ip_allowed(client_ip: str, allowed_cidrs: list[str]) -> bool:
    """Return True if client_ip is allowed by the allowlist.

    Empty allowlist = no restriction (all IPs allowed).
    Loopback (127.x, ::1) is always allowed.
    """
    if not allowed_cidrs:
        return True

    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False  # Invalid IP format — deny

    # Always allow loopback
    if ip.is_loopback:
        return True

    for cidr in allowed_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            if ip in network:
                return True
        except ValueError:
            continue  # Skip malformed CIDR entries

    return False
