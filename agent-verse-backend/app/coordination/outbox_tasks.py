"""Beat task: deliver the coordination outbox to Redis Streams (COORD-OUTBOX)."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)


async def dispatch_coordination_outbox_once() -> dict[str, Any]:
    import redis.asyncio as aioredis

    from app.coordination.outbox_dispatcher import CoordinationOutboxDispatcher
    from app.coordination.streams import CoordinationStreams
    from app.core.config import get_settings
    from app.db.session import get_session_factory, get_system_session_factory

    redis_url = (get_settings().redis_url or "").strip()
    client = aioredis.from_url(redis_url, decode_responses=True) if redis_url else None
    try:
        if client is not None:
            await client.ping()  # fail closed: no reachable transport, no claims
        dispatcher = CoordinationOutboxDispatcher(
            session_factory=get_session_factory(),
            system_session_factory=get_system_session_factory(),
            publisher=lambda: CoordinationStreams(client) if client is not None else None,
        )
        return await dispatcher.dispatch_once()
    finally:
        if client is not None:
            await client.aclose()


@celery_app.task(name="agentverse.coordination.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_coordination_outbox() -> dict[str, Any]:
    """Claim due outbox rows per tenant and publish them (retry/backoff/dead-letter).

    Runs on ``run_in_fresh_loop``: ``asyncio.run`` closed the loop without
    disposing the engines, so this beat task (every few seconds) left pooled
    asyncpg connections on a dead loop and broke the NEXT task on the worker
    ("Event loop is closed" / "attached to a different loop").
    """
    from app.db import session as db_session

    try:
        result = db_session.run_in_fresh_loop(dispatch_coordination_outbox_once())
    except Exception as exc:
        _log.warning("coordination_outbox_dispatch_failed", error=str(exc)[:200])
        return {"status": "error", "error": type(exc).__name__}
    if result.get("delivered"):
        _log.info("coordination_outbox_dispatched", **result)
    return result


__all__ = ["dispatch_coordination_outbox", "dispatch_coordination_outbox_once"]
