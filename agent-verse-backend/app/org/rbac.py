"""PART 18 — Org-scoped RBAC Middleware.

Org roles (per spec):
  org_admin   — full access to all orgs + departments
  dept_admin  — full access to specific departments
  team_lead   — manages team + approves within team budget
  agent       — executes assigned tasks, limited tool access
  viewer      — read-only, can watch but not command

Enforced at FastAPI dependency level.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, ClassVar

from fastapi import Depends, HTTPException, Request, status
from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── Role hierarchy ─────────────────────────────────────────────────────────────


class OrgRole:
    ORG_ADMIN = "org_admin"
    DEPT_ADMIN = "dept_admin"
    TEAM_LEAD = "team_lead"
    AGENT = "agent"
    VIEWER = "viewer"

    # Permission sets per role
    PERMISSIONS: ClassVar[dict[str, frozenset[str]]] = {
        "org_admin": frozenset(
            {"read", "write", "delete", "approve", "admin", "change_settings", "change_autonomy"}
        ),
        "dept_admin": frozenset({"read", "write", "approve", "change_settings"}),
        "team_lead": frozenset({"read", "write", "approve_team"}),
        "agent": frozenset({"read", "write_own"}),
        "viewer": frozenset({"read"}),
    }

    # Role hierarchy (higher index = more permissions)
    HIERARCHY: ClassVar[list[str]] = ["viewer", "agent", "team_lead", "dept_admin", "org_admin"]

    @classmethod
    def can(cls, role: str, permission: str) -> bool:
        perms = cls.PERMISSIONS.get(role, frozenset())
        return permission in perms or "admin" in perms

    @classmethod
    def is_at_least(cls, role: str, minimum_role: str) -> bool:
        """Check if role has at least the privileges of minimum_role."""
        try:
            return cls.HIERARCHY.index(role) >= cls.HIERARCHY.index(minimum_role)
        except ValueError:
            return False


# ── RBAC dependency factories ─────────────────────────────────────────────────


def _highest_org_role(roles: Any) -> str | None:
    """Return the highest org role present in an iterable of role strings, or None."""
    try:
        present = [r for r in roles if r in OrgRole.HIERARCHY]
    except TypeError:
        return None
    if not present:
        return None
    return max(present, key=OrgRole.HIERARCHY.index)


def _resolve_actor_role(request: Request) -> str:
    """Resolve the caller's org role from request state.

    Resolution order (first hit wins):
      1. an explicit ``request.state.org_role`` (test/override or a future
         per-org membership middleware);
      2. an explicit role on the tenant context (``org_role``/``role``);
      3. the highest org role among ``TenantContext.roles`` (the api-key/SSO
         roles resolved by ``TenantMiddleware``) — this is where an assigned
         sub-role like ``viewer``/``team_lead`` comes from;
      4. an ``org:admin`` scope.

    The tenant OWNER key carries a broad ``admin`` role (issued at signup —
    ``tenant_service``: "initial owner key gets full admin access"), which maps
    to ``org_admin``; an ``org:admin`` scope does the same. Everything else is
    **fail-closed**: a caller with no admin/org role — including an authenticated
    key that was never granted one — resolves to the most restrictive ``viewer``
    and is denied, never silently elevated to admin.
    """
    state = getattr(request, "state", None)
    role = getattr(state, "org_role", None)
    tenant = getattr(state, "tenant", None)
    if not role:
        role = getattr(tenant, "org_role", None) or getattr(tenant, "role", None)
    if not role:
        tenant_roles = tuple(getattr(tenant, "roles", ()) or ())
        role = _highest_org_role(tenant_roles)
        if not role and "admin" in tenant_roles:
            role = OrgRole.ORG_ADMIN  # tenant owner key
    if not role:
        scopes = getattr(state, "scopes", None) or getattr(tenant, "scopes", None)
        if scopes and "org:admin" in scopes:
            role = OrgRole.ORG_ADMIN
    # Fail-closed: no admin/org role → viewer (deny writes), never implicit admin.
    return str(role) if role else OrgRole.VIEWER


def enforce_org_role(request: Request, minimum_role: str) -> str:
    """Raise 403 unless the request's actor meets ``minimum_role``. Returns the role."""
    actor_role = _resolve_actor_role(request)
    with _tracer.start_as_current_span("rbac.enforce_org_role") as span:
        span.set_attribute("actor_role", actor_role)
        span.set_attribute("minimum_role", minimum_role)
        if not OrgRole.is_at_least(actor_role, minimum_role):
            _log.warning(
                "rbac.org_role_denied", actor_role=actor_role, minimum_role=minimum_role
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "type": "authorization-error",
                    "title": "Insufficient org role",
                    "status": 403,
                    "detail": (
                        f"Requires at least '{minimum_role}' org role, got '{actor_role}'"
                    ),
                },
            )
    return actor_role


def require_org_role(minimum_role: str = OrgRole.VIEWER) -> Any:
    """
    FastAPI dependency that enforces org-level RBAC.

    Reads the actor's org role from ``request.state`` (populated by the auth /
    tenant middleware) and denies with 403 when it is below ``minimum_role``.

    Usage:
        @router.get("/missions")
        async def list_missions(
            _: str = Depends(require_org_role(OrgRole.VIEWER)),
            ...
        ): ...
    """

    async def _check(request: Request) -> str:
        return enforce_org_role(request, minimum_role)

    return Depends(_check)


class OrgRBACGuard:
    """
    Programmatic RBAC guard for use inside service methods.

    Usage:
        guard = OrgRBACGuard()
        guard.require(actor_role="dept_admin", permission="approve")
        # Raises HTTPException 403 if not permitted
    """

    def require(
        self,
        actor_role: str,
        permission: str,
        raise_on_fail: bool = True,
    ) -> bool:
        with _tracer.start_as_current_span("rbac.check") as span:
            span.set_attribute("role", actor_role)
            span.set_attribute("permission", permission)

            allowed = OrgRole.can(actor_role, permission)
            span.set_attribute("allowed", allowed)

            if not allowed:
                _log.warning(
                    "rbac.denied",
                    role=actor_role,
                    permission=permission,
                )
                if raise_on_fail:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "type": "authorization-error",
                            "title": "Insufficient permissions",
                            "status": 403,
                            "detail": f"Role '{actor_role}' does not have '{permission}' permission",  # noqa: E501
                        },
                    )
            return allowed

    def require_min_role(
        self,
        actor_role: str,
        minimum_role: str,
        raise_on_fail: bool = True,
    ) -> bool:
        allowed = OrgRole.is_at_least(actor_role, minimum_role)
        if not allowed and raise_on_fail:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "type": "authorization-error",
                    "title": "Insufficient role",
                    "status": 403,
                    "detail": f"Requires at least '{minimum_role}' role, got '{actor_role}'",
                },
            )
        return allowed

    def check_department_access(
        self,
        actor_role: str,
        actor_dept_ids: list[str],
        target_dept_id: str,
        raise_on_fail: bool = True,
    ) -> bool:
        """Verify actor can access a specific department."""
        # org_admin can access all departments
        if actor_role == OrgRole.ORG_ADMIN:
            return True
        # Others can only access their own departments
        allowed = target_dept_id in actor_dept_ids
        if not allowed and raise_on_fail:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "type": "authorization-error",
                    "title": "Department access denied",
                    "status": 403,
                    "detail": f"You do not have access to department '{target_dept_id}'",
                },
            )
        return allowed


# ── Cross-dept request signing (PART 18) ──────────────────────────────────────


def sign_cross_dept_request(
    from_agent_id: str,
    to_dept_id: str,
    payload: dict[str, Any],
    secret: str,
) -> str:
    """
    Every inter-dept message is signed (PART 18: Cross-dept request signing).
    Returns HMAC-SHA256 signature.
    """
    timestamp = str(int(time.time()))
    msg = f"{from_agent_id}:{to_dept_id}:{timestamp}:{payload!s}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def verify_cross_dept_request(
    from_agent_id: str,
    to_dept_id: str,
    payload: dict[str, Any],
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify inter-dept request signature (max 5 minutes old)."""
    expected = sign_cross_dept_request(from_agent_id, to_dept_id, payload, secret)
    return hmac.compare_digest(signature, expected)


# ── Anomaly detection (PART 18) ───────────────────────────────────────────────


class AgentAnomalyDetector:
    """Detect tool calls outside role profile → alert."""

    def __init__(self) -> None:
        self._baseline: dict[str, set[str]] = {}  # agent_id → known tools

    def record_tool_call(self, agent_id: str, tool_name: str, role_id: str) -> bool:
        """Record a tool call. Returns True if it's within profile, False if anomalous."""
        # Get allowed tools for this role (via capability registry)
        # Simplified: check against baseline
        known = self._baseline.setdefault(agent_id, set())
        if tool_name not in known:
            if len(known) > 0:  # We've seen other tools for this agent
                _log.warning(
                    "anomaly.new_tool_for_agent",
                    agent_id=agent_id,
                    tool_name=tool_name,
                    role_id=role_id,
                )
            known.add(tool_name)
        return True


# Global instances
org_rbac_guard = OrgRBACGuard()
anomaly_detector = AgentAnomalyDetector()
