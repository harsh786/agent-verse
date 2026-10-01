"""Celery beat task that replays goal events parked in the Redis outbox (SVC-08).

``EventStore.append_event`` raises when an event cannot be stored; the API
(``GoalService._persist_event``) and the worker then park the event in the
``goal_event_outbox`` Redis list instead of dropping it. The beat entry
``drain-goal-event-outbox`` (celery_app.py) runs this on the maintenance queue;
each tick appends a bounded, oldest-first batch and stops at the first failure.
"""

from __future__ import annotations

from typing import Any, cast

from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import celery_app


async def _drain_once() -> dict[str, int]:
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.services.event_store import EventStore, drain_event_outbox

    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        return await drain_event_outbox(EventStore(get_session_factory()), redis)
    finally:
        await redis.aclose()


@celery_app.task(
    name="app.scaling.event_outbox_tasks.drain_goal_event_outbox",
    bind=True,
    max_retries=0,
    queue="maintenance",
)
@beat_task_guard(lock_ttl_seconds=120)
def drain_goal_event_outbox(self: Any) -> dict[str, Any]:
    from app.scaling.tasks import _run_async

    return cast(dict[str, Any], _run_async(_drain_once()))


__all__ = ["drain_goal_event_outbox"]
