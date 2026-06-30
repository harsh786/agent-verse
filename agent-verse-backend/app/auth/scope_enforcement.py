"""ScopeEnforcementMiddleware — enforces API key scopes on every request.

Pipeline (in order):
  1. Exempt path? → skip enforcement
  2. No tenant context? → 401
  3. IP allowlist check (Redis cache, DB fallback) → 403 if blocked
  4. Resolve required scope from ENDPOINT_SCOPES registry
  5. No scope required for this endpoint → pass through
  6. Check Redis permission cache for {tenant_id}:{key_id}
  7. Cache miss → load from DB + role-based fallback, store with TTL=300s
  8. Scope check → 403 with missing scope info
  9. (ABAC conditions evaluated per role assignment — future extension point)

Redis is read from ``request.app.state._rate_limiter_redis`` per request
(Amendment 2.2) so the per-replica Redis upgrade in lifespan is always current.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth.ip_allowlist import IPAllowlistCache, is_ip_allowed
from app.auth.permission_cache import PermissionCache
from app.observability.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Scope registry: (HTTP method, path prefix) → required scope
# ---------------------------------------------------------------------------
ENDPOINT_SCOPES: dict[tuple[str, str], str] = {
    # Goals
    ("GET", "/goals"): "goals:read",
    ("POST", "/goals"): "goals:write",
    ("DELETE", "/goals"): "goals:delete",
    ("PATCH", "/goals"): "goals:write",
    ("PUT", "/goals"): "goals:write",
    # Agents
    ("GET", "/agents"): "agents:read",
    ("POST", "/agents"): "agents:write",
    ("DELETE", "/agents"): "agents:delete",
    ("PATCH", "/agents"): "agents:write",
    ("PUT", "/agents"): "agents:write",
    # Knowledge
    ("GET", "/knowledge"): "knowledge:read",
    ("POST", "/knowledge"): "knowledge:write",
    ("DELETE", "/knowledge"): "knowledge:delete",
    ("PATCH", "/knowledge"): "knowledge:write",
    # Connectors (MCP)
    ("GET", "/connectors"): "mcp:read",
    ("POST", "/connectors"): "mcp:write",
    ("DELETE", "/connectors"): "mcp:write",
    ("PATCH", "/connectors"): "mcp:write",
    # Governance & HITL
    ("GET", "/governance"): "governance:read",
    ("POST", "/governance"): "governance:write",
    ("DELETE", "/governance"): "governance:write",
    ("PATCH", "/governance"): "governance:write",
    # Analytics
    ("GET", "/analytics"): "audit:read",
    # Tenancy settings
    ("GET", "/tenants/me"): "tenancy:read",
    ("PATCH", "/tenants/me"): "tenancy:write",
    ("POST", "/tenants/me"): "tenancy:write",
    ("DELETE", "/tenants/me"): "tenancy:write",
    # Templates (goal templates)
    ("GET", "/templates"): "goals:read",
    ("POST", "/templates"): "goals:write",
    ("DELETE", "/templates"): "goals:delete",
    # Workflows
    ("GET", "/workflows"): "goals:read",
    ("POST", "/workflows"): "goals:write",
    ("DELETE", "/workflows"): "goals:delete",
    ("PATCH", "/workflows"): "goals:write",
    # Schedules
    ("GET", "/schedules"): "goals:read",
    ("POST", "/schedules"): "goals:write",
    ("DELETE", "/schedules"): "goals:delete",
    # Insights
    ("GET", "/insights"): "audit:read",
    # Civilization
    ("GET", "/civilizations"): "agents:read",
    ("POST", "/civilizations"): "agents:write",
    ("DELETE", "/civilizations"): "agents:delete",
}

# Paths that bypass scope enforcement entirely
EXEMPT_PATH_PREFIXES: frozenset[str] = frozenset({
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/",
    "/tenants/signup",
    "/integrations/",   # webhook receivers use their own auth
})

# ---------------------------------------------------------------------------
# Role → scope mapping: fallback when no api_key_scopes rows exist
# ---------------------------------------------------------------------------
_ALL_SCOPES: frozenset[str] = frozenset({
    "goals:read", "goals:write", "goals:delete", "goals:execute",
    "agents:read", "agents:write", "agents:delete",
    "knowledge:read", "knowledge:write", "knowledge:delete",
    "governance:read", "governance:write", "governance:approve",
    "tenancy:read", "tenancy:write",
    "audit:read", "audit:export",
    "costs:read", "costs:admin",
    "mcp:read", "mcp:write",
})

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "admin": _ALL_SCOPES,
    "operator": frozenset({
        "goals:read", "goals:write", "goals:execute",
        "agents:read", "agents:write",
        "knowledge:read", "knowledge:write",
        "mcp:read", "mcp:write",
        "tenancy:read",
    }),
    "approver": frozenset({
        "goals:read",
        "governance:read", "governance:approve",
        "tenancy:read",
        "audit:read",
    }),
    "viewer": frozenset({
        "goals:read",
        "agents:read",
        "knowledge:read",
        "governance:read",
        "tenancy:read",
        "costs:read",
        "audit:read",
        "mcp:read",
    }),
}


# ---------------------------------------------------------------------------
# ABAC condition evaluator
# ---------------------------------------------------------------------------

class ABACEvaluator:
    """Evaluates attribute-based conditions attached to role assignments.

    Supported condition keys:
      department_match (bool): user.department == resource.department
      ownership (str): "creator" — resource.created_by == user.user_id
      time_window (dict): {start: "HH:MM", end: "HH:MM", tz: "TZ"}
    """

    async def evaluate(
        self,
        conditions: dict[str, Any],
        user_ctx: dict[str, Any],
        resource_ctx: dict[str, Any],
    ) -> bool:
        if not conditions:
            return True

        checks: list[bool] = []

        if conditions.get("department_match"):
            checks.append(
                user_ctx.get("department") == resource_ctx.get("department")
            )

        if "ownership" in conditions and conditions["ownership"] == "creator":
            checks.append(
                str(resource_ctx.get("created_by")) == str(user_ctx.get("user_id"))
            )

        if "time_window" in conditions:
            import zoneinfo
            from datetime import datetime

            tw = conditions["time_window"]
            tz = zoneinfo.ZoneInfo(tw.get("tz", "UTC"))
            now = datetime.now(tz)
            current = now.strftime("%H:%M")
            checks.append(tw.get("start", "00:00") <= current <= tw.get("end", "23:59"))

        return all(checks) if checks else True


# ---------------------------------------------------------------------------
# Role resolver — full permission set including inheritance chain
# ---------------------------------------------------------------------------

class RoleResolver:
    """Resolve the complete permission set for a role, traversing parent chain."""

    async def resolve(
        self,
        role_id: str,
        db: Any,
        _visited: set[str] | None = None,
    ) -> set[str]:
        if _visited is None:
            _visited = set()
        if role_id in _visited:
            return set()  # cycle guard
        _visited.add(role_id)

        from sqlalchemy import select

        from app.db.models.auth import CustomRole

        row = await db.execute(
            select(CustomRole).where(
                CustomRole.id == role_id,
                CustomRole.is_active.is_(True),
            )
        )
        role = row.scalar_one_or_none()
        if not role:
            return set()

        perms: set[str] = set(role.permissions or [])
        if role.parent_role_id:
            parent = await self.resolve(role.parent_role_id, db, _visited)
            perms |= parent
        return perms


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class ScopeEnforcementMiddleware(BaseHTTPMiddleware):
    """Enforces API key scopes and IP allowlist on every non-exempt request.

    Redis is fetched from ``request.app.state._rate_limiter_redis`` on each
    dispatch call so the lifespan Redis upgrade is always picked up.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # 1. Exempt paths bypass scope enforcement entirely
        if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        # 2. Require tenant context (set by TenantMiddleware, which runs first)
        tenant = getattr(request.state, "tenant", None)
        if tenant is None:
            # TenantMiddleware will have already returned 401; this is a safety net.
            return await call_next(request)

        tenant_id: str = str(tenant.tenant_id)
        key_id: str = str(tenant.api_key_id)

        # Read Redis per-request so the lifespan upgrade is always current
        redis = getattr(request.app.state, "_rate_limiter_redis", None)

        # 3. IP allowlist check (only when Redis is available)
        if redis is not None:
            ip_cache = IPAllowlistCache(redis)
            tenant_svc = getattr(request.app.state, "tenant_service", None)
            db_factory = getattr(tenant_svc, "_db", None) if tenant_svc else None
            cidrs = await ip_cache.get_cidrs(tenant_id, db_factory=db_factory)
            if cidrs:
                client_ip = self._client_ip(request)
                if not is_ip_allowed(client_ip, cidrs):
                    logger.warning(
                        "ip_blocked",
                        tenant_id=tenant_id,
                        ip=client_ip,
                        path=path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "IP_NOT_ALLOWED",
                            "message": (
                                f"Source IP {client_ip} is not permitted for this tenant"
                            ),
                        },
                    )

        # 4. Backward-compat guard: skip scope enforcement for legacy / test keys
        #    that carry no role assignments.  Keys without roles were issued before
        #    RBAC was introduced and should not be denied — we treat them as
        #    "allow all" until they are explicitly assigned a role or scope list.
        #    Keys *with* roles (e.g. "viewer", "operator") still go through the
        #    full enforcement path below so role-based restrictions are honoured.
        tenant_roles: tuple[str, ...] = getattr(tenant, "roles", ())
        if not tenant_roles:
            return await call_next(request)

        # 5. Determine required scope for this endpoint
        required = self._required_scope(request.method, path)
        if required is None:
            # No scope requirement registered for this endpoint → pass through
            return await call_next(request)

        # 6. Resolve effective scopes (cache-first)
        granted: set[str] | None = None
        if redis is not None:
            perm_cache = PermissionCache(redis)
            granted = await perm_cache.get(tenant_id, key_id)

        if granted is None:
            # Cache miss (or Redis unavailable) → load from DB + role fallback
            tenant_svc = getattr(request.app.state, "tenant_service", None)
            db_factory = getattr(tenant_svc, "_db", None) if tenant_svc else None
            granted = await self._load_scopes(
                db_factory=db_factory,
                tenant_id=tenant_id,
                key_id=key_id,
                roles=getattr(tenant, "roles", ()),
            )
            if redis is not None:
                perm_cache = PermissionCache(redis)
                await perm_cache.set(tenant_id, key_id, granted)

        # 6. Scope check
        if required not in granted:
            logger.info(
                "scope_denied",
                tenant_id=tenant_id,
                key_id=key_id,
                required=required,
                path=path,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "INSUFFICIENT_SCOPE",
                    "detail": f"Insufficient scope: requires {required}",
                    "required_scope": required,
                    "granted_scopes": sorted(granted),
                },
            )

        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Extract the real client IP, respecting trusted proxy headers."""
        for header in ("X-Forwarded-For", "X-Real-IP"):
            val = request.headers.get(header)
            if val:
                return val.split(",")[0].strip()
        return request.client.host if request.client else "0.0.0.0"

    @staticmethod
    def _ip_allowed(client_ip: str, cidrs: list[str]) -> bool:
        """Thin wrapper — delegates to :func:`app.auth.ip_allowlist.is_ip_allowed`."""
        return is_ip_allowed(client_ip, cidrs)

    @staticmethod
    def _required_scope(method: str, path: str) -> str | None:
        """Return the scope required for (method, path), or None if unregistered."""
        # Exact match first
        if (method, path) in ENDPOINT_SCOPES:
            return ENDPOINT_SCOPES[(method, path)]
        # Prefix match (longest wins)
        best: tuple[int, str] | None = None
        for (m, p), scope in ENDPOINT_SCOPES.items():
            if method == m and path.startswith(p):
                length = len(p)
                if best is None or length > best[0]:
                    best = (length, scope)
        return best[1] if best else None

    @staticmethod
    async def _load_scopes(
        db_factory: Any,
        tenant_id: str,
        key_id: str,
        roles: tuple[str, ...],
    ) -> set[str]:
        """Load effective scopes for a key from DB + role-based fallback.

        Resolution order:
          1. Explicit api_key_scopes table entries for this key.
          2. Role assignments in the role_assignments table (new RBAC).
          3. Role-based fallback from TenantContext.roles (backward compat).
        """
        scopes: set[str] = set()

        if db_factory is not None:
            try:
                from sqlalchemy import select, text as _text

                from app.db.models.auth import APIKeyScope, CustomRole, RoleAssignment

                async with db_factory() as db:
                    # Set RLS tenant context so the api_key_scopes isolation policy passes
                    await db.execute(
                        _text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant_id},
                    )
                    # Direct key scopes
                    rows = await db.execute(
                        select(APIKeyScope.scope).where(
                            APIKeyScope.api_key_id == key_id,
                            APIKeyScope.tenant_id == tenant_id,
                        )
                    )
                    for (scope,) in rows.fetchall():
                        scopes.add(scope)

                    # Role-based scopes via assignments (CRITICAL: filter by user_id=key_id)
                    role_rows = await db.execute(
                        select(CustomRole.permissions)
                        .join(
                            RoleAssignment,
                            RoleAssignment.role_id == CustomRole.id,
                        )
                        .where(
                            RoleAssignment.tenant_id == tenant_id,
                            RoleAssignment.user_id == key_id,  # FIX: was missing
                            RoleAssignment.revoked_at.is_(None),
                        )
                    )
                    for (perms,) in role_rows.fetchall():
                        if perms:
                            scopes.update(perms)
            except Exception:
                pass  # DB unavailable — fall through to role-based fallback

        # Fallback: derive scopes from TenantContext.roles (backward compat)
        if not scopes and roles:
            for role in roles:
                scopes.update(ROLE_SCOPES.get(role, frozenset()))

        # If still empty (no scopes anywhere), derive from roles unconditionally
        if not scopes:
            for role in roles:
                scopes.update(ROLE_SCOPES.get(role, frozenset()))

        return scopes
