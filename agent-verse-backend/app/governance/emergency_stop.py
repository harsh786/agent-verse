"""Emergency-stop flags: one place that defines the keys and how they are read.

Two writers exist:

* ``POST /governance/emergency-stop`` → ``emergency_stop:{tenant_id}`` (tenant-wide)
* ``POST /orgs/{org_id}/emergency-stop`` → ``emergency_stop:{tenant_id}:{org_id}``

The only reader used to check the tenant key alone, so an org stop answered
"stopped" while that org's goals kept running. Every execution path (the Celery
worker and in-process API execution via ``AgentGraph.run``) now asks this module,
which honours both: a tenant stop blocks every goal of the tenant, an org stop
blocks goals whose ``execution_context.org_id`` is that org.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

TENANT_STOP_REASON = "Emergency stop active for tenant"
ORG_STOP_REASON = "Emergency stop active for organization"
ORG_UNVERIFIED_REASON = (
    "Emergency stop active for an organization of this tenant and the goal's "
    "organization could not be verified"
)


def tenant_stop_key(tenant_id: str) -> str:
    return f"emergency_stop:{tenant_id}"


def org_stop_key(tenant_id: str, org_id: str) -> str:
    return f"emergency_stop:{tenant_id}:{org_id}"


def _is_set(value: Any) -> bool:
    """A Redis flag value (bytes/str/int). Anything else (e.g. a mock) is unset."""
    return isinstance(value, (bytes, str, int)) and bool(value)


def _stopped_org_ids_sync(redis: Any, tenant_id: str) -> set[str]:
    prefix = f"{tenant_stop_key(tenant_id)}:"
    out: set[str] = set()
    for raw in redis.scan_iter(match=f"{prefix}*", count=100):
        if not isinstance(raw, (bytes, str)):
            continue
        key = raw.decode() if isinstance(raw, bytes) else str(raw)
        if key.startswith(prefix) and len(key) > len(prefix):
            out.add(key[len(prefix):])
    return out


def emergency_stop_reason_sync(
    redis: Any,
    tenant_id: str,
    *,
    org_id: str | None = None,
    resolve_org_id: Callable[[], str | None] | None = None,
) -> str | None:
    """Return why a goal must not start, or ``None`` if it may run.

    ``resolve_org_id`` is only called when at least one org of the tenant is
    stopped (so the common path costs one GET + one SCAN, no DB). If it raises
    while an org stop is active the goal is blocked (fail closed): a kill
    switch that is skipped whenever a lookup hiccups is not a kill switch.
    """
    if redis is None:
        return None
    if _is_set(redis.get(tenant_stop_key(tenant_id))):
        return TENANT_STOP_REASON
    stopped = _stopped_org_ids_sync(redis, tenant_id)
    if not stopped:
        return None
    goal_org = org_id
    if goal_org is None and resolve_org_id is not None:
        try:
            goal_org = resolve_org_id()
        except Exception as exc:
            _log.warning("emergency_stop_org_resolve_failed", error=str(exc))
            return ORG_UNVERIFIED_REASON
    if goal_org and str(goal_org) in stopped:
        return ORG_STOP_REASON
    return None


async def emergency_stop_reason(redis: Any, tenant_id: str, org_id: str | None) -> str | None:
    """Async variant for in-process execution, where the goal's org is known."""
    if redis is None:
        return None
    if _is_set(await redis.get(tenant_stop_key(tenant_id))):
        return TENANT_STOP_REASON
    if org_id and _is_set(await redis.get(org_stop_key(tenant_id, str(org_id)))):
        return ORG_STOP_REASON
    return None


async def goal_org_id(db_factory: Any, tenant_id: str, goal_id: str) -> str | None:
    """Read ``goals.execution_context->>'org_id'`` under the tenant RLS GUC."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db_factory() as session, sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT execution_context->>'org_id' FROM goals "
                    "WHERE id = :gid AND tenant_id = :tid"
                ),
                {"gid": goal_id, "tid": tenant_id},
            )
        ).fetchone()
    return str(row[0]) if row and row[0] else None
