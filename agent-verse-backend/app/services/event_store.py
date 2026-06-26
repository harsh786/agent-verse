"""Durable goal event storage."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

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
        async with self._db() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_ctx.tenant_id
        ):
            result = await session.execute(
                select(func.max(GoalEvent.sequence)).where(
                    GoalEvent.tenant_id == tenant_ctx.tenant_id,
                    GoalEvent.goal_id == goal_id,
                )
            )
            current_sequence = result.scalar_one_or_none() or 0
            session.add(
                GoalEvent(
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=goal_id,
                    sequence=current_sequence + 1,
                    event_type=str(event.get("type", "unknown")),
                    payload=dict(event),
                )
            )

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
