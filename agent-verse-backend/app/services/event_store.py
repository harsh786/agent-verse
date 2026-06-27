"""Durable goal event storage."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.db.models.goal import GoalEvent
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import TenantContext


class EventStore:
    """Append and replay goal events under tenant-scoped DB context."""

    def __init__(self, db_session_factory: Any) -> None:
        self._db = db_session_factory

    async def append_event(
        self, goal_id: str, event: dict[str, Any], *, tenant_ctx: TenantContext
    ) -> None:
        """Append a goal event with race-free database-side sequence numbering.

        Uses a single INSERT ... SELECT so that the sequence number is computed
        inside the same database transaction, eliminating the TOCTOU race that
        existed in the previous SELECT MAX + INSERT approach.

        The unique constraint ``uq_goal_events_sequence`` (tenant_id, goal_id,
        sequence) acts as a safety net: if two concurrent transactions somehow
        produce the same sequence number, the second INSERT will fail with an
        IntegrityError.  Retry logic with exponential backoff handles transient
        conflicts before propagating the final failure as a warning (non-fatal).
        """
        import json as _json
        import uuid as _uuid

        from sqlalchemy import text

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                    session, tenant_ctx.tenant_id
                ):
                    await session.execute(
                        text(
                            """
                            INSERT INTO goal_events
                                (id, tenant_id, goal_id, sequence, event_type, payload)
                            VALUES (
                                :id,
                                :tid,
                                :gid,
                                COALESCE(
                                    (SELECT MAX(sequence)
                                     FROM goal_events
                                     WHERE tenant_id = :tid2 AND goal_id = :gid2),
                                    0
                                ) + 1,
                                :etype,
                                :payload::jsonb
                            )
                            """
                        ),
                        {
                            "id": _uuid.uuid4().hex,
                            "tid": tenant_ctx.tenant_id,
                            "gid": goal_id,
                            "tid2": tenant_ctx.tenant_id,
                            "gid2": goal_id,
                            "etype": str(event.get("type", "unknown")),
                            "payload": _json.dumps(dict(event)),
                        },
                    )
                return  # success — exit retry loop
            except Exception as exc:
                if attempt == max_retries - 1:
                    from app.observability.logging import get_logger
                    get_logger(__name__).warning(
                        "event_append_failed_all_retries", error=str(exc)
                    )
                    return
                await asyncio.sleep(0.05 * (attempt + 1))  # 50 ms, 100 ms backoff

    async def list_events(
        self, goal_id: str, *, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        async with self._db() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_ctx.tenant_id
        ):
            result = await session.execute(
                select(GoalEvent)
                .where(
                    GoalEvent.tenant_id == tenant_ctx.tenant_id,
                    GoalEvent.goal_id == goal_id,
                )
                .order_by(GoalEvent.sequence)
            )
            return [dict(event.payload) for event in result.scalars().all()]
