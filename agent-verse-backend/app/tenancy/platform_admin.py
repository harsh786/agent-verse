"""Who is a platform (deployment) admin.

Platform-admin actions are deployment-wide (cross-tenant administration, the
global model registry). There are two ways to be a platform admin:

* **tenant admin of an operator tenant** — the caller has the ``admin`` role and
  its tenant is listed in ``PLATFORM_ADMIN_TENANT_IDS`` (comma-separated; ``*`` =
  any tenant, for a single-tenant deployment). While the variable is unset, any
  tenant admin qualifies outside production; production requires the list.
* **the platform admin key** — ``PLATFORM_ADMIN_KEY`` presented as ``X-Admin-Key``.

Status codes describe the CALLER: 403 when not authorized (never 401 — the web
client treats a 401 as "session expired" and logs the user out), 503 when an
admin key is presented but the deployment has none configured.

Same rule as the model registry's ``_registry_access``
(``app/api/model_registry.py``); this module is the shared implementation.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import HTTPException, Request

_FORBIDDEN = (
    "Platform admin privileges required: sign in as an admin of the operator "
    "tenant, or enter the platform admin key"
)


def _admin_tenant_ids() -> set[str] | None:
    """``PLATFORM_ADMIN_TENANT_IDS`` as a set; ``None`` when unset."""
    raw = os.getenv("PLATFORM_ADMIN_TENANT_IDS")
    if raw is None or not raw.strip():
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() == "production"


def platform_admin_access(
    request: Request | Any, *, forbidden: str = _FORBIDDEN
) -> tuple[bool, str | None, str, int]:
    """Whether the caller is a platform admin: ``(allowed, via, reason, status)``.

    ``via`` is ``"tenant_admin"`` or ``"admin_key"`` when allowed. *forbidden* is
    the refusal message for a caller that is neither (callers may name the
    action, e.g. "modify the model registry").
    """
    from app.tenancy.rbac import has_role

    ctx = getattr(request.state, "tenant", None)
    if ctx is not None and getattr(ctx, "roles", None) and has_role(ctx, "admin"):
        allowed = _admin_tenant_ids()
        if allowed is None and not _is_production():
            return True, "tenant_admin", "", 200
        if allowed is not None and ("*" in allowed or ctx.tenant_id in allowed):
            return True, "tenant_admin", "", 200
        tenant_reason = (
            "this tenant is not a platform operator tenant (add it to "
            "PLATFORM_ADMIN_TENANT_IDS) or enter the platform admin key"
        )
    else:
        tenant_reason = forbidden

    presented = request.headers.get("x-admin-key", "")
    if not presented:
        return False, None, tenant_reason, 403
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        return (
            False,
            None,
            "Platform admin key is not configured on this deployment (set "
            "PLATFORM_ADMIN_KEY), so it cannot be used",
            503,
        )
    if not hmac.compare_digest(presented.encode(), admin_key.encode()):
        return False, None, "The platform admin key is incorrect", 403
    return True, "admin_key", "", 200


def require_platform_admin(request: Request) -> str:
    """FastAPI dependency: raise 403/503 unless the caller is a platform admin.

    Returns how the caller qualified (``"tenant_admin"`` / ``"admin_key"``).
    """
    allowed, via, reason, status = platform_admin_access(request)
    if not allowed:
        raise HTTPException(status_code=status, detail=reason)
    return via or ""
