"""CivilizationBus — Redis pub/sub event bus with PostgreSQL persistence.

Topics: spawn, findings, debate, coordination, lifecycle
Channel format: civ:{tenant_id}:{civilization_id}:{topic}

Every message is persisted to bus_messages for replay.
No business logic — pure pub/sub + persistence.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_VALID_TOPICS = frozenset({"spawn", "findings", "debate", "coordination", "lifecycle", "system"})


class CivilizationBus:
    """Redis pub/sub bus for civilization events with durable persistence."""

    def __init__(
        self,
        *,
        civilization_id: str,
        tenant_id: str,
        redis: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._civ_id = civilization_id
        self._tenant_id = tenant_id
        self._redis = redis
        self._db = db_session_factory

    def _channel(self, topic: str) -> str:
        return f"civ:{self._tenant_id}:{self._civ_id}:{topic}"

    def _all_channel(self) -> str:
        return f"civ:{self._tenant_id}:{self._civ_id}:*"

    async def publish(
        self,
        *,
        from_agent_id: str,
        topic: str,
        payload: dict[str, Any],
    ) -> str:
        """Publish a message to the bus. Persists to DB and publishes to Redis."""
        if topic not in _VALID_TOPICS:
            topic = "system"

        message_id = uuid.uuid4().hex
        full_payload = {
            "id": message_id,
            "civilization_id": self._civ_id,
            "tenant_id": self._tenant_id,
            "from_agent_id": from_agent_id,
            "topic": topic,
            "payload": payload,
            "ts": datetime.now(UTC).isoformat(),
        }

        # Persist to DB
        await self._persist_message(message_id, from_agent_id, topic, payload)

        # Publish to Redis
        if self._redis is not None:
            try:
                channel = self._channel(topic)
                await self._redis.publish(channel, json.dumps(full_payload))
            except Exception as exc:
                logger.warning("civ_bus_publish_redis_failed", topic=topic, error=str(exc))

        # Also emit as civilization_event for SSE/replay
        await self._emit_civilization_event(topic, full_payload)

        return message_id

    async def subscribe(
        self,
        topics: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to topics. Yields messages as dicts."""
        if self._redis is None:
            return

        channels = []
        if topics:
            channels = [self._channel(t) for t in topics if t in _VALID_TOPICS]
        else:
            # Subscribe to all topics via pattern
            channels = [self._channel(t) for t in _VALID_TOPICS]

        async with _nullctx(self._redis) as r:
            pubsub = r.pubsub() if hasattr(r, "pubsub") else self._redis.pubsub()
            await pubsub.subscribe(*channels)
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        yield data
                    except Exception:
                        pass
            finally:
                await pubsub.unsubscribe(*channels)

    async def get_messages(
        self,
        *,
        topic: str | None = None,
        from_agent_id: str | None = None,
        limit: int = 100,
        since_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch persisted messages from DB for replay."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text

            conditions = ["civilization_id = :cid", "tenant_id = :tid"]
            params: dict[str, Any] = {
                "cid": self._civ_id,
                "tid": self._tenant_id,
                "limit": limit,
            }
            if topic:
                conditions.append("topic = :topic")
                params["topic"] = topic
            if from_agent_id:
                conditions.append("from_agent_id = :from_agent")
                params["from_agent"] = from_agent_id
            if since_ts:
                conditions.append("ts > :since")
                params["since"] = since_ts

            where = " AND ".join(conditions)
            async with self._db() as session:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT id, from_agent_id, topic, payload, ts FROM bus_messages "
                            f"WHERE {where} ORDER BY ts DESC LIMIT :limit"
                        ),
                        params,
                    )
                ).fetchall()
            return [
                {
                    "id": r[0],
                    "from_agent_id": r[1],
                    "topic": r[2],
                    "payload": r[3] if isinstance(r[3], dict) else {},
                    "ts": r[4].isoformat() if r[4] else "",
                    "civilization_id": self._civ_id,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("civ_bus_get_messages_failed", error=str(exc))
            return []

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _persist_message(
        self, message_id: str, from_agent_id: str, topic: str, payload: dict
    ) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO bus_messages
                            (id, civilization_id, tenant_id, from_agent_id, topic, payload, ts)
                        VALUES (:id, :cid, :tid, :from, :topic, :payload::jsonb, NOW())
                    """),
                    {
                        "id": message_id,
                        "cid": self._civ_id,
                        "tid": self._tenant_id,
                        "from": from_agent_id,
                        "topic": topic,
                        "payload": json.dumps(payload),
                    },
                )
        except Exception as exc:
            logger.warning("civ_bus_persist_failed", error=str(exc))

    async def _emit_civilization_event(self, event_type: str, payload: dict) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with self._db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO civilization_events
                            (id, civilization_id, tenant_id, type, payload, ts)
                        VALUES (:id, :cid, :tid, :type, :payload::jsonb, NOW())
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "cid": self._civ_id,
                        "tid": self._tenant_id,
                        "type": f"bus.{event_type}",
                        "payload": json.dumps(payload),
                    },
                )
        except Exception as exc:
            logger.warning("civ_bus_event_emit_failed", error=str(exc))


class _nullctx:
    """Null async context manager to handle redis clients directly."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> None:
        pass
