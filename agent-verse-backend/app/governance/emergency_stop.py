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

import contextlib
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


def stop_channel(tenant_id: str) -> str:
    """Pub/sub channel announcing a newly activated stop of *tenant_id* (or one of
    its orgs). Runners subscribe so a stop interrupts them immediately instead of
    at their next poll; the persisted flag stays the source of truth (INC-04)."""
    return f"emergency_stop:activated:{tenant_id}"


def _tenant_of_key(key: str) -> str:
    prefix = "emergency_stop:"
    return key[len(prefix) :].split(":", 1)[0] if key.startswith(prefix) else ""


def _is_set(value: Any) -> bool:
    """A Redis flag value (bytes/str/int). Anything else (e.g. a mock) is unset."""
    return isinstance(value, (bytes, str, int)) and bool(value)


def org_stop_index_key(tenant_id: str) -> str:
    """Per-tenant SET of the org ids that have an active stop (WF-17)."""
    return f"emergency_stop_orgs:{tenant_id}"


# Set once the org-stop index has been rebuilt from flags written before it
# existed (one keyspace SCAN per deployment, not per goal start).
ORG_STOP_INDEX_READY_KEY = "emergency_stop_orgs:__index_ready__"


def _decode(raw: Any) -> str | None:
    if isinstance(raw, bytes):
        return raw.decode()
    return raw if isinstance(raw, str) else None


def _backfill_org_stop_index_sync(redis: Any) -> None:
    """Index org stop flags that predate the index (runs once per Redis)."""
    for raw in redis.scan_iter(match="emergency_stop:*:*", count=1000):
        key = _decode(raw)
        if key is None:
            continue
        parts = key.split(":")
        if len(parts) == 3 and parts[1] and parts[2]:
            redis.sadd(org_stop_index_key(parts[1]), parts[2])
    redis.set(ORG_STOP_INDEX_READY_KEY, "1")


def _stopped_org_ids_sync(redis: Any, tenant_id: str) -> set[str]:
    """The tenant's stopped org ids: one SMEMBERS of the per-tenant index.

    Used to SCAN ``emergency_stop:{tenant}:*`` on every goal start — a walk of
    the WHOLE keyspace in 100-key steps (thousands of round trips at millions
    of keys). Errors propagate: the caller fails closed.
    """
    if not _is_set(redis.get(ORG_STOP_INDEX_READY_KEY)):
        _backfill_org_stop_index_sync(redis)
    members = redis.smembers(org_stop_index_key(tenant_id)) or set()
    return {m for m in (_decode(raw) for raw in members) if m}


def emergency_stop_reason_sync(
    redis: Any,
    tenant_id: str,
    *,
    org_id: str | None = None,
    resolve_org_id: Callable[[], str | None] | None = None,
) -> str | None:
    """Return why a goal must not start, or ``None`` if it may run.

    ``resolve_org_id`` is only called when at least one org of the tenant is
    stopped (so the common path costs two GETs + one SMEMBERS, no DB). If it raises
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
    # The flag is the source of truth; the index only narrows the lookup.
    if (
        goal_org
        and str(goal_org) in stopped
        and _is_set(redis.get(org_stop_key(tenant_id, str(goal_org))))
    ):
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
    # Interrupt running goals now (they also re-read the flag at every step
    # boundary and signal poll, so a lost announcement only costs latency).
    tenant_id = _tenant_of_key(key)
    if tenant_id:
        try:
            await redis.publish(stop_channel(tenant_id), key)
        except Exception as exc:
            _log.warning("emergency_stop_announce_failed", key=key, error=str(exc))
    return record


async def activate_org_stop(
    redis: Any, tenant_id: str, org_id: str, *, activated_by: str, reason: str = ""
) -> dict[str, Any]:
    """Org stop: the flag plus its entry in the tenant's org-stop index (WF-17)."""
    record = await activate_stop(
        redis, org_stop_key(tenant_id, org_id), activated_by=activated_by, reason=reason
    )
    try:
        await redis.sadd(org_stop_index_key(tenant_id), str(org_id))
    except Exception as exc:
        # The worker start check reads the index; an unindexed flag would only
        # be honoured at step boundaries. Undo and refuse instead.
        with contextlib.suppress(Exception):
            await redis.delete(org_stop_key(tenant_id, org_id))
        raise EmergencyStopUnavailableError(str(exc)) from exc
    return record


async def clear_org_stop(redis: Any, tenant_id: str, org_id: str) -> None:
    """Lift an org stop: drop the flag first, then its index entry."""
    await clear_stop(redis, org_stop_key(tenant_id, org_id))
    try:
        await redis.srem(org_stop_index_key(tenant_id), str(org_id))
    except Exception as exc:
        # A stale index entry only costs the reader an org lookup; the flag
        # itself is gone, so the org is resumed.
        _log.warning("emergency_stop_index_clear_failed", tenant_id=tenant_id, error=str(exc))


async def record_stop_outcome(
    redis: Any,
    key: str,
    record: dict[str, Any],
    *,
    cancelled_goals: int,
    rejected_approvals: int,
) -> bool:
    """Add the activation's outcome counts to the stored flag (best effort).

    ``GET /governance/emergency-stop`` then reports them to every operator, not
    just the browser that activated the stop. Written with ``SET XX`` so a stop
    that was lifted in the meantime is never re-created. Returns whether the
    counts were stored; a failure leaves the (already persisted) stop intact.
    """
    if redis is None:
        return False
    updated = {
        **record,
        "cancelled_goals": int(cancelled_goals),
        "rejected_approvals": int(rejected_approvals),
    }
    try:
        return bool(await redis.set(key, json.dumps(updated), xx=True))
    except Exception:
        return False


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
    redis: Any, tenant_id: str, org_id: str | None = None, *, org_unverified: bool = False
) -> str | None:
    """Sync (Celery worker) variant of :func:`enforce_emergency_stop`.

    ``org_unverified``: the goal's org could not be read. While any org of the
    tenant is stopped such a goal is stopped too (fail closed, INC-04).
    """
    if redis is None:
        return None
    try:
        if _is_set(redis.get(tenant_stop_key(tenant_id))):
            return TENANT_STOP_REASON
        if org_id and _is_set(redis.get(org_stop_key(tenant_id, str(org_id)))):
            return ORG_STOP_REASON
        if not org_id and org_unverified and _stopped_org_ids_sync(redis, tenant_id):
            return ORG_UNVERIFIED_REASON
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


async def cancel_goals_under_stop(
    db_factory: Any,
    redis: Any,
    tenant_id: str,
    org_id: str | None = None,
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    """Cancel every non-terminal goal of the tenant (or of one org) in keyset batches.

    Runs off the request path (Celery). Per batch: the Redis cancel flag reaches
    each goal's runner on any replica/worker, then one conditional UPDATE marks the
    batch cancelled (a goal that finished meanwhile keeps its real status).
    Returns ``{"scanned": n, "cancelled": m, "signal_failures": k}``; a DB error
    propagates so the task is retried (the stop flag still halts the goals).
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.reliability.goal_lifecycle import signal_cancel

    org_clause = " AND execution_context->>'org_id' = :org" if org_id else ""
    select_sql = (
        "SELECT id FROM goals WHERE tenant_id = :tid "
        "AND status NOT IN ('complete', 'failed', 'cancelled') AND id > :after"
        f"{org_clause} ORDER BY id LIMIT :lim"
    )
    update_sql = (
        "UPDATE goals SET status = 'cancelled', "
        "error_message = 'Cancelled by emergency stop' "
        "WHERE tenant_id = :tid AND id = ANY(:ids) "
        "AND status NOT IN ('complete', 'failed', 'cancelled')"
    )
    after = ""
    scanned = cancelled = signal_failures = 0
    while True:
        params: dict[str, Any] = {"tid": tenant_id, "after": after, "lim": batch_size}
        if org_id:
            params["org"] = str(org_id)
        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_id):
                ids = [str(r[0]) for r in (await session.execute(text(select_sql), params))]
        if not ids:
            break
        scanned += len(ids)
        for gid in ids:
            try:
                await signal_cancel(gid, redis, strict=True)
            except Exception:
                signal_failures += 1
        async with db_factory() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_id):
                res = await session.execute(text(update_sql), {"tid": tenant_id, "ids": ids})
                cancelled += int(getattr(res, "rowcount", 0) or 0)
        after = ids[-1]
        if len(ids) < batch_size:
            break
    _log.info(
        "emergency_stop_goals_cancelled",
        tenant_id=tenant_id,
        org_id=org_id,
        scanned=scanned,
        cancelled=cancelled,
        signal_failures=signal_failures,
    )
    return {"scanned": scanned, "cancelled": cancelled, "signal_failures": signal_failures}
