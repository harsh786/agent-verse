"""Civilization event types and dispatch helpers."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


class CivEventType:
    AGENT_SPAWNED = "agent_spawned"
    AGENT_RETIRED = "agent_retired"
    AGENT_UPDATED = "agent_updated"
    SPAWN_DENIED = "spawn_denied"
    GOAL_SUBMITTED = "goal_submitted"
    GOAL_COMPLETED = "goal_completed"
    DEBATE_STARTED = "debate_started"
    DEBATE_CONCLUDED = "debate_concluded"
    BLACKBOARD_POSTED = "blackboard_posted"
    LEARNING_CANDIDATE = "learning_candidate"
    LEARNING_PROMOTED = "learning_promoted"
    LEARNING_REJECTED = "learning_rejected"
    BREACH_DETECTED = "breach_detected"
    CIVILIZATION_PAUSED = "civilization_paused"
    CIVILIZATION_RESUMED = "civilization_resumed"
    BUS_MESSAGE = "bus_message"


async def emit_event(
    *,
    civilization_id: str,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    db: Any = None,
    redis: Any = None,
) -> str:
    """Emit a civilization event to DB and Redis SSE channel."""
    event_id = uuid.uuid4().hex
    full_event = {
        "id": event_id,
        "civilization_id": civilization_id,
        "tenant_id": tenant_id,
        "type": event_type,
        "payload": payload,
        "ts": datetime.now(UTC).isoformat(),
    }

    # Persist to DB
    if db is not None:
        try:
            from sqlalchemy import text

            async with db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO civilization_events
                            (id, civilization_id, tenant_id, type, payload, ts)
                        VALUES (:id, :cid, :tid, :type, :payload::jsonb, NOW())
                    """),
                    {
                        "id": event_id,
                        "cid": civilization_id,
                        "tid": tenant_id,
                        "type": event_type,
                        "payload": json.dumps(payload),
                    },
                )
        except Exception as exc:
            logger.warning(
                "civ_event_persist_failed", event_type=event_type, error=str(exc)
            )

    # Publish to Redis SSE channel
    if redis is not None:
        try:
            channel = f"civ_sse:{tenant_id}:{civilization_id}"
            await redis.publish(channel, json.dumps(full_event))
        except Exception as exc:
            logger.warning(
                "civ_event_redis_failed", event_type=event_type, error=str(exc)
            )

    return event_id


async def get_events_since(
    *,
    civilization_id: str,
    tenant_id: str,
    since_ts: datetime | None = None,
    event_types: list[str] | None = None,
    limit: int = 500,
    db: Any,
) -> list[dict[str, Any]]:
    """Fetch events from the durable log for SSE reconnect catch-up."""
    if db is None:
        return []
    try:
        from sqlalchemy import text

        conditions = ["civilization_id = :cid", "tenant_id = :tid"]
        params: dict[str, Any] = {
            "cid": civilization_id,
            "tid": tenant_id,
            "limit": limit,
        }
        if since_ts:
            conditions.append("ts > :since")
            params["since"] = since_ts
        if event_types:
            conditions.append("type = ANY(:types)")
            params["types"] = event_types
        where = " AND ".join(conditions)
        async with db() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT id, type, payload, ts FROM civilization_events "
                        f"WHERE {where} ORDER BY ts ASC LIMIT :limit"
                    ),
                    params,
                )
            ).fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "payload": r[2] if isinstance(r[2], dict) else {},
                "ts": r[3].isoformat() if r[3] else "",
                "civilization_id": civilization_id,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("civ_get_events_failed", error=str(exc))
        return []
