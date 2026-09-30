"""Resolve a tenant's real plan tier from the tenant record (TRG-06).

Paths that have no authenticated API key — beat-fired schedules, file drops,
token-authenticated webhooks, integrations — used to guess the plan: schedule
payloads never carried one (every beat fire ran as FREE: 10 fires/h, 2 bulkhead
slots, the free queue) and other paths hard-coded PROFESSIONAL. The plan is a
billing fact that lives on ``tenants.plan_tier``, so it is read from there at
fire time, never taken from a stored or client-supplied value.

No process-local cache: the lookup is one primary-key read, and a cache would
keep serving the old tier on other replicas after a plan change.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.tenancy.context import PlanTier

_log = get_logger(__name__)


def _to_plan(value: Any) -> PlanTier | None:
    raw = str(getattr(value, "value", value) or "").strip().lower()
    try:
        return PlanTier(raw)
    except ValueError:
        return None


async def resolve_tenant_plan(
    tenant_id: str,
    *,
    tenant_service: Any = None,
    db_factory: Any = None,
) -> PlanTier:
    """Return *tenant_id*'s plan tier from the tenant record.

    Tries ``tenant_service.get_tenant`` (DB-authoritative once the lifespan has
    wired it) and then a direct ``SELECT plan_tier FROM tenants`` through
    *db_factory*. When neither can answer, the most restrictive tier (FREE) is
    returned and the failure is logged — an unknown plan never grants a paid
    tier's limits.
    """
    if not tenant_id:
        return PlanTier.FREE

    if tenant_service is not None and hasattr(tenant_service, "get_tenant"):
        try:
            tenant = await tenant_service.get_tenant(tenant_id)
            plan = _to_plan(tenant.get("plan") if isinstance(tenant, dict) else None)
            if plan is not None:
                return plan
        except Exception as exc:
            _log.warning("tenant_plan_service_lookup_failed", tenant_id=tenant_id, error=str(exc))

    if db_factory is not None:
        try:
            from sqlalchemy import text

            # tenants has no RLS policy; the id is the primary key.
            async with db_factory() as session:
                value = (
                    await session.execute(
                        text("SELECT plan_tier FROM tenants WHERE id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar()
            plan = _to_plan(value)
            if plan is not None:
                return plan
        except Exception as exc:
            _log.warning("tenant_plan_db_lookup_failed", tenant_id=tenant_id, error=str(exc))

    _log.warning("tenant_plan_unresolved_using_free", tenant_id=tenant_id)
    return PlanTier.FREE
