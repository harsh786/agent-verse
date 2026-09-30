"""Per-workflow access control (``workflow_permissions`` ACL).

Old bug: grants were stored and listed but never read — update, publish,
trigger and delete were open to anyone with the tenant's workflow scope, so a
"viewer" grant restricted nothing.

Model:
* Levels, weakest first: ``viewer`` (read) < ``runner`` (trigger / control runs)
  < ``editor`` (change, publish, delete) < ``admin`` (manage the ACL).
* An empty ACL means the tenant-wide default: every tenant caller has full access.
* Once a workflow has any grant, a caller needs a grant — for its principal
  (API key id) or for one of its RBAC roles (``subject_type='role'``) — at or
  above the level the endpoint requires. A tenant ``admin`` always passes (so a
  workflow can never be locked out).
* The ACL is read from the database on every check; if it cannot be read the
  request is refused (503), never let through.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request

from app.observability.logging import get_logger

_log = get_logger(__name__)

LEVELS: tuple[str, ...] = ("viewer", "runner", "editor", "admin")
_RANK = {level: i for i, level in enumerate(LEVELS)}


def _caller(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return tenant


def _expanded_roles(ctx: Any) -> frozenset[str]:
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import effective_roles

    raw = tuple(getattr(ctx, "roles", ()) or ())
    return effective_roles(
        TenantContext(tenant_id="", plan=PlanTier.FREE, api_key_id="", roles=raw)
    )


def access_level(acl: list[dict[str, Any]], principal: str, roles: frozenset[str]) -> str | None:
    """The caller's strongest level on a workflow, ``"admin"`` for an empty ACL
    (tenant default) or a tenant admin, ``None`` when it has no grant."""
    if "admin" in roles or not acl:
        return "admin"
    best: str | None = None
    for grant in acl:
        level = str(grant.get("permission") or "")
        if level not in _RANK:
            continue
        subject = str(grant.get("subject_id") or "")
        if str(grant.get("subject_type") or "user") == "role":
            matches = subject in roles
        else:
            matches = bool(principal) and subject == principal
        if matches and (best is None or _RANK[level] > _RANK[best]):
            best = level
    return best


async def caller_access(request: Request, workflow_id: str) -> str | None:
    """Load the ACL and return the caller's level (see :func:`access_level`)."""
    ctx = _caller(request)
    roles = _expanded_roles(ctx)
    if "admin" in roles:
        return "admin"
    svc = getattr(request.app.state, "workflow_service", None)
    if svc is None:
        # No workflow service = nowhere an ACL could be stored: the tenant
        # default applies (the endpoint itself answers 503 if it needs one).
        return "admin"
    try:
        acl = await svc.get_permissions(tenant_id=ctx.tenant_id, workflow_id=workflow_id)
    except Exception as exc:
        _log.error("workflow_acl_read_failed", workflow_id=workflow_id, error=str(exc))
        raise HTTPException(
            status_code=503, detail="Workflow permissions could not be checked"
        ) from exc
    return access_level(list(acl or []), str(getattr(ctx, "api_key_id", "") or ""), roles)


async def require_workflow_access(request: Request, workflow_id: str, needed: str) -> None:
    """403 (and an audit record) unless the caller holds ``needed`` or better."""
    level = await caller_access(request, workflow_id)
    if level is not None and _RANK[level] >= _RANK[needed]:
        return
    ctx = _caller(request)
    principal = str(getattr(ctx, "api_key_id", "") or "")
    _log.info(
        "workflow_access_denied",
        workflow_id=workflow_id,
        principal=principal,
        needed=needed,
        level=level,
    )
    _audit_denial(request, ctx, workflow_id, needed, level)
    raise HTTPException(
        status_code=403,
        detail=f"This action needs '{needed}' access to the workflow"
        + (f" (you have '{level}')" if level else " (you have no access)"),
    )


def _audit_denial(
    request: Request, ctx: Any, workflow_id: str, needed: str, level: str | None
) -> None:
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        return
    try:
        from app.governance.audit import AuditEvent
        from app.governance.permissions import ActionLevel

        audit_log.record(
            AuditEvent(
                goal_id=workflow_id,
                tool_name="workflow.access_denied",
                action_level=ActionLevel.DENY,
                outcome=f"{request.method} {request.url.path}",
                api_key_id=str(getattr(ctx, "api_key_id", "") or ""),
                note=f"needed={needed}; had={level or 'none'}",
            ),
            tenant_ctx=ctx,
        )
    except Exception as exc:  # auditing must never break the call path
        _log.warning("workflow_access_audit_failed", error=str(exc))


def workflow_access(needed: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: require ``needed`` on the ``{workflow_id}`` path param."""
    if needed not in _RANK:
        raise ValueError(f"unknown workflow access level {needed!r}")

    async def dependency(request: Request) -> None:
        await require_workflow_access(request, request.path_params["workflow_id"], needed)

    return dependency


def run_access(needed: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: require ``needed`` on the workflow owning ``{run_id}``."""
    if needed not in _RANK:
        raise ValueError(f"unknown workflow access level {needed!r}")

    async def dependency(request: Request) -> None:
        ctx = _caller(request)
        svc = getattr(request.app.state, "workflow_service", None)
        if svc is None:
            return  # the handler answers 503
        run = await svc.get_run(tenant_id=ctx.tenant_id, run_id=request.path_params["run_id"])
        workflow_id = str((run or {}).get("workflow_id") or "") if isinstance(run, dict) else ""
        if not workflow_id:
            return  # unknown run: the handler answers 404
        await require_workflow_access(request, workflow_id, needed)

    return dependency
