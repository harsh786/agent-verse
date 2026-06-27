"""Blackboard — tenant-scoped shared findings store.

Agents post findings with topic + confidence.
Others query before acting (reduces duplicate work).
Conflicting high-confidence claims → triggers debate.
Optimistic concurrency via version field.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_CONFLICT_CONFIDENCE_THRESHOLD = 0.75  # Both claims must exceed this to trigger debate


class BlackboardConflictError(Exception):
    """Raised when an update fails due to version conflict."""

    def __init__(
        self,
        message: str,
        current_version: int = 0,
        expected_version: int = 0,
    ):
        super().__init__(message)
        self.current_version = current_version
        self.expected_version = expected_version


class Blackboard:
    """Tenant-scoped shared findings board for civilization agents.

    Optimistic concurrency: each entry has a version; updates must provide
    the expected version to prevent conflicts (mirrors CollaborationStore pattern).
    """

    def __init__(
        self,
        *,
        civilization_id: str,
        tenant_id: str,
        db_session_factory: Any = None,
        bus: Any = None,  # CivilizationBus for conflict-triggered debates
    ) -> None:
        self._civ_id = civilization_id
        self._tenant_id = tenant_id
        self._db = db_session_factory
        self._bus = bus
        # In-memory cache for test/dev mode
        self._entries: dict[str, dict] = {}

    async def post(
        self,
        *,
        author_agent_id: str,
        topic: str,
        content: str,
        confidence: float = 0.8,
        refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Post a new finding to the blackboard."""
        entry_id = uuid.uuid4().hex
        entry: dict[str, Any] = {
            "id": entry_id,
            "civilization_id": self._civ_id,
            "tenant_id": self._tenant_id,
            "author_agent_id": author_agent_id,
            "topic": topic,
            "content": content,
            "confidence": min(1.0, max(0.0, confidence)),
            "refs": refs or [],
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
        }

        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session, session.begin():
                    await session.execute(
                        text("""
                            INSERT INTO blackboard_entries
                                (id, civilization_id, tenant_id, author_agent_id, topic,
                                 content, confidence, refs, version, created_at)
                            VALUES
                                (:id, :cid, :tid, :author, :topic,
                                 :content, :confidence, :refs::jsonb, 1, NOW())
                        """),
                        {
                            "id": entry_id,
                            "cid": self._civ_id,
                            "tid": self._tenant_id,
                            "author": author_agent_id,
                            "topic": topic,
                            "content": content,
                            "confidence": entry["confidence"],
                            "refs": json.dumps(refs or []),
                        },
                    )
            except Exception as exc:
                logger.warning("blackboard_post_failed", error=str(exc))
        else:
            self._entries[entry_id] = entry

        # Check for conflicts and trigger debate if necessary
        await self._check_and_handle_conflict(
            topic, content, confidence, author_agent_id, entry_id
        )

        # Publish to bus
        if self._bus is not None:
            try:
                await self._bus.publish(
                    from_agent_id=author_agent_id,
                    topic="findings",
                    payload={
                        "entry_id": entry_id,
                        "topic": topic,
                        "confidence": confidence,
                        "content": content[:200],
                    },
                )
            except Exception as exc:
                logger.warning("blackboard_bus_publish_failed", error=str(exc))

        return entry

    async def update(
        self,
        *,
        entry_id: str,
        author_agent_id: str,
        content: str,
        confidence: float,
        expected_version: int,
    ) -> dict[str, Any]:
        """Update an existing entry with optimistic concurrency check."""
        if self._db is not None:
            try:
                from sqlalchemy import text

                # Atomic update with version check
                async with self._db() as session, session.begin():
                    result = await session.execute(
                        text("""
                            UPDATE blackboard_entries
                            SET content = :content,
                                confidence = :confidence,
                                version = version + 1,
                                author_agent_id = :author
                            WHERE id = :id
                              AND civilization_id = :cid
                              AND tenant_id = :tid
                              AND version = :expected_version
                            RETURNING version
                        """),
                        {
                            "content": content,
                            "confidence": confidence,
                            "author": author_agent_id,
                            "id": entry_id,
                            "cid": self._civ_id,
                            "tid": self._tenant_id,
                            "expected_version": expected_version,
                        },
                    )
                    row = result.fetchone()
                    if row is None:
                        # Version conflict — fetch current version
                        cur = (
                            await session.execute(
                                text(
                                    "SELECT version FROM blackboard_entries "
                                    "WHERE id=:id AND tenant_id=:tid"
                                ),
                                {"id": entry_id, "tid": self._tenant_id},
                            )
                        ).fetchone()
                        current = cur[0] if cur else 0
                        raise BlackboardConflictError(
                            f"Version conflict: expected {expected_version}, got {current}",
                            current_version=current,
                            expected_version=expected_version,
                        )
                    return {"id": entry_id, "version": row[0], "content": content}
            except BlackboardConflictError:
                raise
            except Exception as exc:
                logger.warning("blackboard_update_failed", error=str(exc))
                raise
        else:
            # In-memory fallback
            entry = self._entries.get(entry_id)
            if entry is None:
                raise KeyError(f"Entry {entry_id} not found")
            if entry["version"] != expected_version:
                raise BlackboardConflictError(
                    "Version conflict",
                    current_version=entry["version"],
                    expected_version=expected_version,
                )
            entry["content"] = content
            entry["confidence"] = confidence
            entry["version"] += 1
            return dict(entry)

    async def query(
        self,
        *,
        topic: str | None = None,
        author_agent_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query the blackboard for relevant findings."""
        if self._db is not None:
            try:
                from sqlalchemy import text

                conditions = [
                    "civilization_id = :cid",
                    "tenant_id = :tid",
                    "confidence >= :min_conf",
                ]
                params: dict[str, Any] = {
                    "cid": self._civ_id,
                    "tid": self._tenant_id,
                    "min_conf": min_confidence,
                    "limit": limit,
                }
                if topic:
                    conditions.append("topic = :topic")
                    params["topic"] = topic
                if author_agent_id:
                    conditions.append("author_agent_id = :author")
                    params["author"] = author_agent_id
                where = " AND ".join(conditions)
                async with self._db() as session:
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT id, author_agent_id, topic, content, confidence, "
                                f"refs, version, created_at "
                                f"FROM blackboard_entries "
                                f"WHERE {where} ORDER BY confidence DESC, created_at DESC "
                                f"LIMIT :limit"
                            ),
                            params,
                        )
                    ).fetchall()
                return [
                    {
                        "id": r[0],
                        "author_agent_id": r[1],
                        "topic": r[2],
                        "content": r[3],
                        "confidence": float(r[4] or 0),
                        "refs": r[5] or [],
                        "version": r[6],
                        "created_at": r[7].isoformat() if r[7] else "",
                        "civilization_id": self._civ_id,
                    }
                    for r in rows
                ]
            except Exception as exc:
                logger.warning("blackboard_query_failed", error=str(exc))
                return []
        else:
            results = [
                e
                for e in self._entries.values()
                if e["confidence"] >= min_confidence
                and (topic is None or e["topic"] == topic)
                and (
                    author_agent_id is None
                    or e["author_agent_id"] == author_agent_id
                )
            ]
            return sorted(results, key=lambda x: x["confidence"], reverse=True)[:limit]

    async def _check_and_handle_conflict(
        self,
        topic: str,
        new_content: str,
        new_confidence: float,
        author_agent_id: str,
        new_entry_id: str,
    ) -> None:
        """Check for conflicting high-confidence claims on the same topic."""
        if new_confidence < _CONFLICT_CONFIDENCE_THRESHOLD:
            return
        if self._bus is None:
            return

        existing = await self.query(
            topic=topic,
            min_confidence=_CONFLICT_CONFIDENCE_THRESHOLD,
            limit=5,
        )
        conflicting = [
            e
            for e in existing
            if e["id"] != new_entry_id and e["author_agent_id"] != author_agent_id
        ]
        if conflicting:
            # Trigger debate via bus
            logger.info(
                "blackboard_conflict_detected",
                topic=topic,
                conflicting_count=len(conflicting),
                civilization_id=self._civ_id,
            )
            try:
                await self._bus.publish(
                    from_agent_id="blackboard",
                    topic="debate",
                    payload={
                        "trigger": "blackboard_conflict",
                        "topic": topic,
                        "claim_a": {
                            "entry_id": new_entry_id,
                            "content": new_content[:200],
                            "confidence": new_confidence,
                        },
                        "claim_b": {
                            "entry_id": conflicting[0]["id"],
                            "content": conflicting[0]["content"][:200],
                            "confidence": conflicting[0]["confidence"],
                        },
                    },
                )
            except Exception as exc:
                logger.warning("blackboard_debate_trigger_failed", error=str(exc))
