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


async def sweep_handoffs_once(redis_client: Any = None) -> dict[str, int]:
    """Expire overdue handoffs and re-run failed parent resumes (ORG-38)."""
    from app.coordination.handoffs.membership import DatabaseHandoffMembership
    from app.coordination.handoffs.repository import PostgresHandoffRepository
    from app.coordination.handoffs.resumption import HandoffParentResumer
    from app.coordination.handoffs.service import HandoffService
    from app.coordination.live_bus import CoordinationLiveBus
    from app.coordination.service import CoordinationService
    from app.coordination.store import CoordinationStore
    from app.coordination.transcript.repository import PostgresTranscriptRepository
    from app.coordination.transcript.service import TranscriptService
    from app.db.session import get_session_factory, get_system_session_factory

    sessions = get_session_factory()
    coordination = CoordinationService(CoordinationStore(sessions))
    transcript = TranscriptService(PostgresTranscriptRepository(sessions))
    bus = CoordinationLiveBus(lambda: redis_client)
    resumer = HandoffParentResumer(
        coordination_service=lambda: coordination,
        transcript_service=lambda: transcript,
        live_bus=lambda: bus,
    )
    service = HandoffService(
        PostgresHandoffRepository(sessions, system_session_factory=get_system_session_factory()),
        membership=DatabaseHandoffMembership(lambda: sessions),
        emit_event=resumer.emit_event,
        pause_parent=resumer.pause_parent,
        resume_parent=resumer.resume_parent,
    )
    return await service.sweep()


@celery_app.task(name="agentverse.coordination.sweep_handoffs")  # type: ignore[untyped-decorator]
def sweep_handoffs() -> dict[str, int]:
    """Beat: a crashed handoff target must not leave its parent paused forever.

    Failures raise so Celery records them (the next tick retries; every step is
    idempotent per handoff). Runs on ``run_in_fresh_loop`` (L-01), like the
    outbox dispatcher."""
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.db import session as db_session

    async def _run() -> dict[str, int]:
        redis_url = (get_settings().redis_url or "").strip()
        client = aioredis.from_url(redis_url) if redis_url else None
        try:
            return await sweep_handoffs_once(client)
        finally:
            if client is not None:
                await client.aclose()

    result = db_session.run_in_fresh_loop(_run())
    if result.get("expired") or result.get("resumed") or result.get("errors"):
        _log.info("coordination_handoffs_swept", **result)
    return result


__all__ = [
    "dispatch_coordination_outbox",
    "dispatch_coordination_outbox_once",
    "sweep_handoffs",
    "sweep_handoffs_once",
]
