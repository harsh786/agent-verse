"""Durable goal event storage."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from sqlalchemy import select

from app.core.errors import PlatformError, ServiceUnavailableError
from app.db.models.goal import GoalEvent
from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger
from app.tenancy.context import PlanTier, TenantContext

_log = get_logger(__name__)

# Redis list holding goal events whose durable append failed (newest at the head).
# Drained by the ``drain-goal-event-outbox`` beat task (app/scaling/event_outbox_tasks.py).
GOAL_EVENT_OUTBOX_KEY = "goal_event_outbox"
# Bounded: beyond this the oldest buffered events are trimmed (and counted).
_OUTBOX_MAX = 100_000
# An event that still cannot be appended after this many drain attempts (its goal
# row never appeared) is dropped and counted rather than retried forever.
_OUTBOX_MAX_ATTEMPTS = 20


class EventAppendError(PlatformError):
    """A goal event could not be written to the durable event store."""

    code = "EVENT_STORE_UNAVAILABLE"
    http_status = 503
    retryable = True


def _count(outcome: str, n: int = 1) -> None:
    try:
        from app.observability.metrics import GOAL_EVENT_OUTBOX_TOTAL

        GOAL_EVENT_OUTBOX_TOTAL.labels(outcome=outcome).inc(n)
    except Exception:  # metrics never break the event path
        pass


async def buffer_failed_event(
    redis: Any, *, tenant_id: str, goal_id: str, event: dict[str, Any]
) -> bool:
    """Park an event whose durable append failed in the Redis outbox.

    Returns False (and logs ``goal_event_lost`` loudly) when even the outbox
    write fails: the event then exists only in live delivery.
    """
    envelope = json.dumps(
        {"tenant_id": tenant_id, "goal_id": goal_id, "event": dict(event), "attempts": 0},
        default=str,
    )
    try:
        await redis.lpush(GOAL_EVENT_OUTBOX_KEY, envelope)
        await redis.ltrim(GOAL_EVENT_OUTBOX_KEY, 0, _OUTBOX_MAX - 1)
    except Exception as exc:
        _count("lost")
        _log.error("goal_event_lost", goal_id=goal_id, event_type=event.get("type"), error=str(exc))
        return False
    _count("buffered")
    _log.warning("goal_event_buffered", goal_id=goal_id, event_type=event.get("type"))
    return True


async def buffer_failed_event_via_settings(
    *, tenant_id: str, goal_id: str, event: dict[str, Any], redis: Any = None
) -> bool:
    """:func:`buffer_failed_event` with a one-shot client when none is wired."""
    if redis is not None:
        return await buffer_failed_event(redis, tenant_id=tenant_id, goal_id=goal_id, event=event)
    try:
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    except Exception as exc:
        _count("lost")
        _log.error("goal_event_lost", goal_id=goal_id, error=str(exc))
        return False
    try:
        return await buffer_failed_event(client, tenant_id=tenant_id, goal_id=goal_id, event=event)
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


async def drain_event_outbox(event_store: Any, redis: Any, *, batch: int = 500) -> dict[str, int]:
    """Append buffered events oldest-first; stop at the first failure (the store
    is still down) and put that event back at the oldest end."""
    replayed = dropped = 0
    for _ in range(batch):
        raw = await redis.rpop(GOAL_EVENT_OUTBOX_KEY)
        if raw is None:
            break
        try:
            env = json.loads(raw)
            ctx = TenantContext(
                tenant_id=str(env["tenant_id"]), plan=PlanTier.FREE, api_key_id="event-outbox"
            )
            goal_id, event = str(env["goal_id"]), dict(env["event"])
        except Exception as exc:
            dropped += 1
            _log.error("goal_event_outbox_malformed", error=str(exc))
            continue
        try:
            await event_store.append_event(goal_id, event, tenant_ctx=ctx)
        except Exception as exc:
            env["attempts"] = int(env.get("attempts", 0)) + 1
            if env["attempts"] >= _OUTBOX_MAX_ATTEMPTS:
                dropped += 1
                _log.error(
                    "goal_event_outbox_dropped",
                    goal_id=goal_id,
                    attempts=env["attempts"],
                    error=str(exc),
                )
                continue
            await redis.rpush(GOAL_EVENT_OUTBOX_KEY, json.dumps(env, default=str))
            break
        replayed += 1
    if replayed:
        _count("replayed", replayed)
    if dropped:
        _count("dropped", dropped)
    return {"replayed": replayed, "dropped": dropped}


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
    ) -> int:
        """Append a goal event with a per-goal, gap-free sequence number; return it.

        Raises :class:`EventAppendError` when the event could not be stored
        (after retries, or because the goal row does not exist): it used to be
        logged and dropped, so events silently vanished from the durable stream.

        The number comes from ``goals.event_seq`` (``UPDATE ... RETURNING`` in
        the same statement as the INSERT): the row lock serialises allocation
        per goal, so two concurrent appends can never draw the same number.
        It used to be ``MAX(sequence) + 1``, which races under READ COMMITTED
        (both writers read the same MAX) and leaned on the unique constraint to
        reject the loser. goal_events is now partitioned by month, so that
        constraint can no longer span partitions, and a MAX would probe every
        partition on each append.
        """
        import uuid as _uuid

        from sqlalchemy import text

        max_retries = 3
        last_exc: Exception | None = None
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
                            RETURNING sequence
                            """
                        ),
                        {
                            "id": _uuid.uuid4().hex,
                            "tid": tenant_ctx.tenant_id,
                            "gid": goal_id,
                            "etype": str(event.get("type", "unknown")),
                            "payload": json.dumps(dict(event), default=str),
                        },
                    )
                    seq = result.scalar_one_or_none()
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.05 * (attempt + 1))  # 50 ms, 100 ms backoff
                continue
            if seq is None:
                # No goal row (or not this tenant's): retrying now cannot help;
                # the caller buffers it (the row may appear, e.g. a racing insert).
                raise EventAppendError(f"goal {goal_id} has no row to append events to")
            return int(seq)
        _log.warning("event_append_failed_all_retries", goal_id=goal_id, error=str(last_exc))
        raise EventAppendError(
            f"event for goal {goal_id} could not be stored", cause=last_exc
        ) from last_exc

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

        A database error raises :class:`ServiceUnavailableError` (503): replaying
        an outage as an empty history showed clients a goal with no events.
        """
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
                return [dict(event.payload) for event in result.scalars().all()]
        except Exception as exc:
            _log.warning("list_events_failed", goal_id=goal_id, error=str(exc))
            raise ServiceUnavailableError(
                "Goal event history is temporarily unavailable; retry.",
                code="EVENT_HISTORY_UNAVAILABLE",
                cause=exc,
            ) from exc

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
        unit-test path) or when *tenant_ctx* is not provided. A database error
        raises :class:`ServiceUnavailableError` (503) — never an empty history.
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
            _log.warning("list_events_since_failed", goal_id=goal_id, error=str(exc))
            raise ServiceUnavailableError(
                "Goal event history is temporarily unavailable; retry.",
                code="EVENT_HISTORY_UNAVAILABLE",
                cause=exc,
            ) from exc
