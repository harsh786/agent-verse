"""Event-relative ``relative_delay`` triggers (B1-8).

A ``relative_delay`` trigger with an ``event_channel`` (and no ``fire_at_iso``)
fires ``relative_offset_seconds`` after each event published on that channel
(``POST /triggers/events/{channel}``) — or after the ISO timestamp found at
``relative_to_field`` (a dotted path) in the event's payload, e.g. "remind the
customer 2 days after ``order.delivered_at``".

The EVENT consumer *arms* one row in ``trigger_delayed_fires`` per (trigger,
event); the beat claims the due rows (``FOR UPDATE SKIP LOCKED`` on the partial
``due_at`` index, maintenance session) and fires each through the governed
dispatch with the event as the payload. Deleting the trigger deletes its pending
fires (FK cascade); a paused trigger's fires wait until it is resumed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# An event payload stored for a delayed fire is bounded (the dispatcher's own
# plan payload limits still apply when it fires).
MAX_DELAYED_PAYLOAD_BYTES = 256 * 1024
# Longest event-relative delay: a year.
MAX_EVENT_DELAY_SECONDS = 366 * 86400
# Fired rows are kept this long for audit, then pruned by the beat.
FIRED_RETENTION = dt.timedelta(days=7)


def is_event_armed(trigger_type: str, event_channel: str, fire_at_iso: str) -> bool:
    """True for a relative_delay that is armed by events instead of a fixed time."""
    return (
        trigger_type == "relative_delay"
        and bool((event_channel or "").strip())
        and not (fire_at_iso or "").strip()
    )


def delayed_fire_id(schedule_id: str, event_id: str) -> str:
    """Stable id per (trigger, event): a redelivered event arms once."""
    return hashlib.sha256(f"{schedule_id}:{event_id}".encode()).hexdigest()[:32]


def _lookup(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _parse_instant(value: Any) -> dt.datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def delayed_due_at(
    *,
    offset_seconds: int,
    relative_to_field: str,
    data: dict[str, Any],
    received_at: dt.datetime,
) -> dt.datetime | None:
    """When the fire armed by *data* is due (UTC-aware), or None when the
    configured ``relative_to_field`` is missing / not a timestamp."""
    base: dt.datetime | None
    if (relative_to_field or "").strip():
        base = _parse_instant(_lookup(data, relative_to_field.strip()))
        if base is None:
            return None
    else:
        base = received_at if received_at.tzinfo else received_at.replace(tzinfo=dt.UTC)
    return base + dt.timedelta(seconds=int(offset_seconds or 0))


async def arm_delayed_fire(
    db_factory: Any,
    *,
    tenant_id: str,
    schedule_id: str,
    event_id: str,
    due_at: dt.datetime,
    payload: dict[str, Any],
) -> bool:
    """Insert one pending fire (tenant RLS context); False when it already
    existed (a redelivered event). Raises on a database error so the event is
    redelivered rather than lost."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    body = json.dumps(payload, default=str)
    if len(body.encode()) > MAX_DELAYED_PAYLOAD_BYTES:
        raise ValueError(
            f"event payload is larger than {MAX_DELAYED_PAYLOAD_BYTES} bytes; not armed"
        )
    async with db_factory() as session, session.begin(), sqlalchemy_rls_context(
        session, tenant_id
    ):
        result = await session.execute(
            text(
                "INSERT INTO trigger_delayed_fires "
                "(id, tenant_id, schedule_id, event_id, due_at, payload) "
                "SELECT :id, s.tenant_id, s.id, :eid, :due, CAST(:payload AS jsonb) "
                "FROM schedules s WHERE s.id = :sid AND s.tenant_id = :tid "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": delayed_fire_id(schedule_id, event_id),
                "tid": tenant_id,
                "sid": schedule_id,
                "eid": event_id[:200],
                "due": due_at,
                "payload": body,
            },
        )
    return bool(getattr(result, "rowcount", 0))


async def claim_due_delayed_fires(
    system_db_factory: Any, now: dt.datetime, *, limit: int
) -> list[dict[str, Any]]:
    """Claim (mark fired) the due fires of active, unpaused triggers of active
    tenants in one statement; returns each claimed fire with its schedule row
    (``schedule`` key, a mapping of the ``schedules`` columns)."""
    from sqlalchemy import text

    from app.db.rls import system_session

    due = now if now.tzinfo else now.replace(tzinfo=dt.UTC)
    async with system_db_factory() as session, session.begin(), system_session(session):
        claimed = (
            (
                await session.execute(
                    text(
                        "UPDATE trigger_delayed_fires AS f SET fired_at = :now "
                        "FROM (SELECT d.id FROM trigger_delayed_fires d "
                        "JOIN schedules s ON s.id = d.schedule_id "
                        "AND s.tenant_id = d.tenant_id AND NOT s.paused "
                        "JOIN tenants t ON t.id = d.tenant_id AND t.is_active "
                        "WHERE d.fired_at IS NULL AND d.due_at <= :now "
                        "ORDER BY d.due_at LIMIT :lim FOR UPDATE OF d SKIP LOCKED) AS due "
                        "WHERE f.id = due.id "
                        "RETURNING f.id, f.tenant_id, f.schedule_id, f.event_id, f.due_at, "
                        "f.payload, f.created_at"
                    ),
                    {"now": due, "lim": int(limit)},
                )
            )
            .mappings()
            .all()
        )
        if not claimed:
            return []
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM schedules "
                        "WHERE id = ANY(CAST(:ids AS varchar[])) "
                        "AND tenant_id = ANY(CAST(:tids AS varchar[]))"
                    ),
                    {
                        "ids": sorted({str(c["schedule_id"]) for c in claimed}),
                        "tids": sorted({str(c["tenant_id"]) for c in claimed}),
                    },
                )
            )
            .mappings()
            .all()
        )
    by_key = {(str(r["tenant_id"]), str(r["id"])): dict(r) for r in rows}
    out: list[dict[str, Any]] = []
    for c in claimed:
        sched = by_key.get((str(c["tenant_id"]), str(c["schedule_id"])))
        if sched is not None:
            out.append({**dict(c), "schedule": sched})
    return out


async def release_delayed_fires(system_db_factory: Any, ids: list[str]) -> None:
    """Un-claim fires whose enqueue failed so the next tick retries them."""
    if not ids:
        return
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db_factory() as session, session.begin(), system_session(session):
        await session.execute(
            text(
                "UPDATE trigger_delayed_fires SET fired_at = NULL "
                "WHERE id = ANY(CAST(:ids AS varchar[]))"
            ),
            {"ids": ids},
        )


async def prune_fired_delayed_fires(
    system_db_factory: Any, now: dt.datetime, *, limit: int = 1000
) -> int:
    """Delete at most *limit* fires fired before the retention window."""
    from sqlalchemy import text

    from app.db.rls import system_session

    cutoff = (now if now.tzinfo else now.replace(tzinfo=dt.UTC)) - FIRED_RETENTION
    async with system_db_factory() as session, session.begin(), system_session(session):
        result = await session.execute(
            text(
                "DELETE FROM trigger_delayed_fires WHERE id IN ("
                "SELECT id FROM trigger_delayed_fires "
                "WHERE fired_at IS NOT NULL AND fired_at < :cutoff LIMIT :lim)"
            ),
            {"cutoff": cutoff, "lim": int(limit)},
        )
    return int(getattr(result, "rowcount", 0) or 0)
