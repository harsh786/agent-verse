"""Durable outbox for ``audit_log`` writes that exhausted their retries (a03-F058-01).

``AuditLog.record`` (the sync entry point the agent executor uses) schedules a
tracked, retried INSERT. A write that still failed was only logged and counted
(``audit_write_lost``): a Postgres outage longer than the retry window silently
removed governed actions from the trail. Such a row is now parked in a Redis
list and replayed into ``audit_log`` by the ``drain-audit-write-outbox`` beat
task. The INSERT is idempotent on the event id (``ON CONFLICT (id) DO NOTHING``),
so a replay after an unknown commit outcome never duplicates a row, and the
SIEM outbox copy is written in the same transaction as on the live path.

A row is never dropped: the outbox refuses new rows past a size cap (the write
is then counted as lost, as before), and a row that keeps failing is moved to a
dead-letter list for an operator instead of blocking the queue.
"""

from __future__ import annotations

import json
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

# Newest at the head (LPUSH); drained oldest-first (RPOP).
AUDIT_OUTBOX_KEY = "audit_write_outbox"
AUDIT_OUTBOX_DEAD_KEY = "audit_write_outbox:dead"
# Past this many parked rows a new one is refused (never evicts an older row).
AUDIT_OUTBOX_MAX = 1_000_000
# A row still failing after this many drain attempts goes to the dead list.
AUDIT_OUTBOX_MAX_ATTEMPTS = 50


async def park_audit_row(redis: Any, *, tenant_id: str, row: dict[str, Any]) -> bool:
    """Park one ``audit_log`` row whose write failed. False: not parked."""
    if redis is None:
        return False
    envelope = json.dumps({"tenant_id": tenant_id, "row": row, "attempts": 0}, default=str)
    try:
        if int(await redis.llen(AUDIT_OUTBOX_KEY)) >= AUDIT_OUTBOX_MAX:
            _log.error("audit_outbox_full", tenant_id=tenant_id, event_id=row.get("id"))
            return False
        await redis.lpush(AUDIT_OUTBOX_KEY, envelope)
    except Exception as exc:
        _log.error("audit_outbox_park_failed", event_id=row.get("id"), error=str(exc)[:200])
        return False
    return True


def _event_from_row(row: dict[str, Any]) -> Any:
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel

    return AuditEvent(
        goal_id=str(row.get("goal_id") or ""),
        tool_name=str(row.get("tool_name") or ""),
        action_level=ActionLevel(row["action_level"]),
        outcome=str(row.get("outcome") or ""),
        step_id=str(row.get("step_id") or ""),
        approver=row.get("approver"),
        note=str(row.get("note") or ""),
        event_id=str(row["id"]),
        ip_address=row.get("ip_address"),
        user_agent=row.get("user_agent"),
        api_key_id=row.get("api_key_id"),
        request_id=row.get("request_id"),
        connector_id=row.get("connector_id"),
    )


async def drain_audit_outbox(audit_log: Any, redis: Any, *, batch: int = 500) -> dict[str, int]:
    """Replay parked rows oldest-first; stop at the first failure (DB still down).

    The failed row goes back to the oldest end with its attempt count bumped; a
    row past :data:`AUDIT_OUTBOX_MAX_ATTEMPTS` (or unreadable) is dead-lettered.
    """
    replayed = dead = 0
    for _ in range(max(1, batch)):
        raw = await redis.rpop(AUDIT_OUTBOX_KEY)
        if raw is None:
            break
        try:
            env = json.loads(raw)
            tenant_id = str(env["tenant_id"])
            event = _event_from_row(dict(env["row"]))
        except Exception as exc:
            dead += 1
            await redis.lpush(AUDIT_OUTBOX_DEAD_KEY, raw)
            _log.error("audit_outbox_malformed", error=str(exc)[:200])
            continue
        try:
            await audit_log._db_record(event, tenant_id)
        except Exception as exc:
            env["attempts"] = int(env.get("attempts", 0)) + 1
            if env["attempts"] >= AUDIT_OUTBOX_MAX_ATTEMPTS:
                dead += 1
                await redis.lpush(AUDIT_OUTBOX_DEAD_KEY, json.dumps(env, default=str))
                _log.error(
                    "audit_outbox_dead_lettered",
                    event_id=event.event_id,
                    attempts=env["attempts"],
                    error=str(exc)[:200],
                )
                continue
            await redis.rpush(AUDIT_OUTBOX_KEY, json.dumps(env, default=str))
            break
        replayed += 1
    if replayed:
        _log.info("audit_outbox_replayed", count=replayed)
    return {"replayed": replayed, "dead_lettered": dead}


__all__ = [
    "AUDIT_OUTBOX_DEAD_KEY",
    "AUDIT_OUTBOX_KEY",
    "drain_audit_outbox",
    "park_audit_row",
]
