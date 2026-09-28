"""Durable goal event storage."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.db.models.goal import GoalEvent
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import TenantContext

# Bounded default so a single replay never walks an unbounded event history. A goal
# realistically emits at most hundreds of events, so this only ever caps a runaway;
# callers needing more page via ``after_sequence`` (or use ``list_events_since``,
# which carries ``_seq`` cursors).
_DEFAULT_EVENT_LIMIT = 10_000


class EventStore:
    """Append and replay goal events under tenant-scoped DB context."""

    def __init__(self, db_session_factory: Any = None) -> None:
        self._db = db_session_factory

    async def append_event(
        self, goal_id: str, event: dict[str, Any], *, tenant_ctx: TenantContext
    ) -> None:
        """Append a goal event with a per-goal, gap-free sequence number.

        The number comes from ``goals.event_seq`` (``UPDATE ... RETURNING`` in
        the same statement as the INSERT): the row lock serialises allocation
        per goal, so two concurrent appends can never draw the same number.
        It used to be ``MAX(sequence) + 1``, which races under READ COMMITTED
        (both writers read the same MAX) and leaned on the unique constraint to
        reject the loser. goal_events is now partitioned by month, so that
        constraint can no longer span partitions, and a MAX would probe every
        partition on each append.
        """
        import json as _json
        import uuid as _uuid

        from sqlalchemy import text

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    result = await session.execute(
                        text(
                            """
                            WITH seq AS (
                                UPDATE goals SET event_seq = event_seq + 1
                                WHERE id = :gid AND tenant_id = :tid
                                RETURNING event_seq
                            )
                            INSERT INTO goal_events
                                (id, tenant_id, goal_id, sequence, event_type, payload)
                            SELECT :id, :tid, :gid, seq.event_seq, :etype,
                                   CAST(:payload AS jsonb)
                            FROM seq
                            """
                        ),
                        {
                            "id": _uuid.uuid4().hex,
                            "tid": tenant_ctx.tenant_id,
                            "gid": goal_id,
                            "etype": str(event.get("type", "unknown")),
                            "payload": _json.dumps(dict(event)),
                        },
                    )
                if getattr(result, "rowcount", 1) == 0:
                    # No goal row (or not this tenant's): the old INSERT failed
                    # the goals FK here; report it the same way, don't retry.
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning(
                        "event_append_goal_missing", goal_id=goal_id
                    )
                return  # success — exit retry loop
            except Exception as exc:
                if attempt == max_retries - 1:
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning("event_append_failed_all_retries", error=str(exc))
                    return
                await asyncio.sleep(0.05 * (attempt + 1))  # 50 ms, 100 ms backoff

    async def list_events(
        self,
        goal_id: str,
        *,
        tenant_ctx: TenantContext,
        limit: int = _DEFAULT_EVENT_LIMIT,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        """Replay a goal's event payloads in sequence order.

        Bounded by ``limit`` (default :data:`_DEFAULT_EVENT_LIMIT`) so the query
        never walks an unbounded history. For full replay of a very long history,
        page with ``after_sequence`` (each call returns events with
        ``sequence > after_sequence``) — the same keyset ``list_events_since``
        uses, whose rows additionally carry a ``_seq`` cursor. The payload shape
        returned here is unchanged (no ``_seq`` added) for backward compatibility.
        """
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                select(GoalEvent)
                .where(
                    GoalEvent.tenant_id == tenant_ctx.tenant_id,
                    GoalEvent.goal_id == goal_id,
                    GoalEvent.sequence > after_sequence,
                )
                .order_by(GoalEvent.sequence)
                .limit(limit)
            )
            return [dict(event.payload) for event in result.scalars().all()]

    async def list_events_since(
        self,
        goal_id: str,
        after_sequence: int,
        limit: int = 100,
        *,
        tenant_ctx: TenantContext | None = None,
    ) -> list[dict[str, Any]]:
        """Return events with sequence > after_sequence for a goal.

        Each returned dict includes a ``_seq`` key with the event's sequence number
        so callers can emit SSE ``id:`` lines for resume support.

        Returns an empty list when no DB session factory is configured (in-memory /
        unit-test path) or when *tenant_ctx* is not provided.
        """
        if self._db is None or tenant_ctx is None:
            return []
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                result = await session.execute(
                    select(GoalEvent)
                    .where(
                        GoalEvent.tenant_id == tenant_ctx.tenant_id,
                        GoalEvent.goal_id == goal_id,
                        GoalEvent.sequence > after_sequence,
                    )
                    .order_by(GoalEvent.sequence)
                    .limit(limit)
                )
                rows = result.scalars().all()
                return [{**dict(row.payload), "_seq": row.sequence} for row in rows]
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("list_events_since_failed", error=str(exc))
            return []
