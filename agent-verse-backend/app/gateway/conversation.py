"""Multi-turn ConversationManager — maintains context across channels."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class ConversationTurn:
    role: str  # "user" | "assistant"
    content: str
    timestamp: str  # ISO 8601
    channel: str
    command_id: str | None = None


@dataclass
class Conversation:
    id: str
    tenant_id: str
    org_id: str | None
    channel: str
    channel_user_id: str | None
    conversation_key: str | None
    turns: list[ConversationTurn]
    context_summary: str | None
    last_command_at: datetime | None
    created_at: datetime


class ConversationManager:
    """
    Maintains conversation state across multiple turns, regardless of channel.
    Uses gateway_conversations DB table.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create(
        self,
        tenant_id: str,
        org_id: str,
        channel: str,
        channel_user_id: str | None,
        conversation_key: str | None,
    ) -> Conversation:
        with _tracer.start_as_current_span("conversation_manager.get_or_create") as span:
            span.set_attribute("channel", channel)
            span.set_attribute("org_id", org_id)

            try:
                from sqlalchemy import text

                # Try to find existing conversation
                q = text("""
                    SELECT id, turns, context_summary, last_command_at, created_at
                    FROM gateway_conversations
                    WHERE tenant_id = :tid AND org_id = :oid AND channel = :ch
                      AND (channel_user_id = :cuid OR (:cuid IS NULL AND channel_user_id IS NULL))
                      AND (conversation_key = :ckey OR (:ckey IS NULL AND conversation_key IS NULL))
                    ORDER BY last_command_at DESC NULLS LAST
                    LIMIT 1
                """)
                res = await self._s.execute(
                    q,
                    {
                        "tid": tenant_id,
                        "oid": org_id,
                        "ch": channel,
                        "cuid": channel_user_id,
                        "ckey": conversation_key,
                    },
                )
                row = res.fetchone()
                if row:
                    turns_raw: list[dict[str, Any]] = row[1] or []
                    turns = [ConversationTurn(**t) for t in turns_raw]
                    return Conversation(
                        id=str(row[0]),
                        tenant_id=tenant_id,
                        org_id=org_id,
                        channel=channel,
                        channel_user_id=channel_user_id,
                        conversation_key=conversation_key,
                        turns=turns,
                        context_summary=row[2],
                        last_command_at=row[3],
                        created_at=row[4],
                    )
            except Exception as exc:
                _log.warning("conversation_manager.query_failed", error=str(exc))

            # Create new in-memory conversation (DB may not have table yet)
            return Conversation(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                org_id=org_id,
                channel=channel,
                channel_user_id=channel_user_id,
                conversation_key=conversation_key,
                turns=[],
                context_summary=None,
                last_command_at=None,
                created_at=datetime.now(UTC),
            )

    async def add_turn(
        self,
        conversation_id: str,
        tenant_id: str,
        role: str,
        content: str,
        channel: str,
        command_id: str | None = None,
    ) -> None:
        turn = ConversationTurn(
            role=role,
            content=content,
            timestamp=datetime.now(UTC).isoformat(),
            channel=channel,
            command_id=command_id,
        )
        try:
            from sqlalchemy import text

            q = text("""
                UPDATE gateway_conversations
                SET turns = turns || CAST(:new_turn AS jsonb),
                    last_command_at = NOW(),
                    updated_at = NOW()
                WHERE id = :cid AND tenant_id = :tid
            """)
            import json

            await self._s.execute(
                q,
                {
                    "cid": conversation_id,
                    "tid": tenant_id,
                    "new_turn": json.dumps(asdict(turn)),
                },
            )
            await self._s.commit()
        except Exception as exc:
            _log.warning("conversation_manager.add_turn_failed", error=str(exc))

    async def get_context(
        self,
        conversation_id: str,
        last_n: int = 10,
    ) -> list[ConversationTurn]:
        try:
            from sqlalchemy import text

            q = text("SELECT turns FROM gateway_conversations WHERE id = :cid")
            res = await self._s.execute(q, {"cid": conversation_id})
            row = res.fetchone()
            if not row:
                return []
            turns_raw: list[dict[str, Any]] = row[0] or []
            turns = [ConversationTurn(**t) for t in turns_raw]
            return turns[-last_n:]
        except Exception as exc:
            _log.warning("conversation_manager.get_context_failed", error=str(exc))
            return []
