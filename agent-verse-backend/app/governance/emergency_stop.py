"""Emergency-stop flags: one place that defines the keys and how they are read.

Two writers exist:

* ``POST /governance/emergency-stop`` → ``emergency_stop:{tenant_id}`` (tenant-wide)
* ``POST /orgs/{org_id}/emergency-stop`` → ``emergency_stop:{tenant_id}:{org_id}``

The only reader used to check the tenant key alone, so an org stop answered
"stopped" while that org's goals kept running. Every execution path (the Celery
worker and in-process API execution via ``AgentGraph.run``) now asks this module,
which honours both: a tenant stop blocks every goal of the tenant, an org stop
blocks goals whose ``execution_context.org_id`` is that org.

Enforcement points: goal submission (``GoalService.submit_goal``), goal start
(worker ``run_goal`` and ``AgentGraph.run``) and every step/wave boundary (the
pause gates installed by ``GoalService`` and the worker). Flags carry no TTL —
a stop lasts until an operator lifts it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
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


# ---------------------------------------------------------------------------
# Writing / reading the stop as durable state
# ---------------------------------------------------------------------------

UNVERIFIABLE_REASON = "Emergency stop state could not be verified"


class EmergencyStopUnavailableError(Exception):
    """The stop cannot be set, cleared or read: it cannot be enforced (fail closed)."""


def _decode_flag(raw: Any) -> dict[str, Any] | None:
    if not _is_set(raw):
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {}  # legacy "1" flag
    data["active"] = True
    return data


async def activate_stop(
    redis: Any, key: str, *, activated_by: str, reason: str = ""
) -> dict[str, Any]:
    """Persist a stop flag WITHOUT a TTL: it lasts until explicitly lifted.

    The tenant flag used to expire after 300 s (the org flag after 24 h), so a
    kill switch silently turned itself off. Raises
    :class:`EmergencyStopUnavailableError` when the flag cannot be written.
    """
    if redis is None:
        raise EmergencyStopUnavailableError("no shared control store (Redis) is configured")
    record = {
        "active": True,
        "activated_at": datetime.now(UTC).isoformat(),
        "activated_by": activated_by,
        "reason": reason,
    }
    try:
        # Plain SET (no EX/KEEPTTL) also discards the TTL of an older flag.
        await redis.set(key, json.dumps(record))
    except Exception as exc:
        raise EmergencyStopUnavailableError(str(exc)) from exc
    return record


async def clear_stop(redis: Any, key: str) -> None:
    if redis is None:
        raise EmergencyStopUnavailableError("no shared control store (Redis) is configured")
    try:
        await redis.delete(key)
    except Exception as exc:
        raise EmergencyStopUnavailableError(str(exc)) from exc


async def read_stop(redis: Any, key: str) -> dict[str, Any] | None:
    """The stored stop record, ``None`` when not stopped; raises when unreadable."""
    if redis is None:
        raise EmergencyStopUnavailableError("no shared control store (Redis) is configured")
    try:
        raw = await redis.get(key)
    except Exception as exc:
        raise EmergencyStopUnavailableError(str(exc)) from exc
    return _decode_flag(raw)


async def enforce_emergency_stop(
    redis: Any, tenant_id: str, org_id: str | None = None
) -> str | None:
    """Fail-closed check for execution paths: why work must stop, or ``None``.

    With no Redis wired no stop can have been activated (activation refuses
    without it), so there is nothing to enforce. A Redis *error* is not "not
    stopped": it returns :data:`UNVERIFIABLE_REASON`.
    """
    if redis is None:
        return None
    try:
        return await emergency_stop_reason(redis, tenant_id, org_id)
    except Exception as exc:
        _log.warning("emergency_stop_check_failed", tenant_id=tenant_id, error=str(exc))
        return UNVERIFIABLE_REASON


def enforce_emergency_stop_sync(
    redis: Any, tenant_id: str, org_id: str | None = None
) -> str | None:
    """Sync (Celery worker) variant of :func:`enforce_emergency_stop`."""
    if redis is None:
        return None
    try:
        if _is_set(redis.get(tenant_stop_key(tenant_id))):
            return TENANT_STOP_REASON
        if org_id and _is_set(redis.get(org_stop_key(tenant_id, str(org_id)))):
            return ORG_STOP_REASON
    except Exception as exc:
        _log.warning("emergency_stop_check_failed", tenant_id=tenant_id, error=str(exc))
        return UNVERIFIABLE_REASON
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
