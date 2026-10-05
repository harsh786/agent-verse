"""SVC-05 integration (Postgres + Redis): a client reconnecting to ANOTHER replica
with Last-Event-ID mid-run gets every later event exactly once — through a
history longer than one replay page, an event the owner publishes while the
replay is reading, and the live tail.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_sse_resume_cursor_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.event_store import EventStore
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _goal_row(db: Any) -> tuple[TenantContext, str]:
    tid, gid = uuid.uuid4().hex, uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e)"),
            {"t": tid, "e": f"{tid}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                "VALUES (:g, :t, 'x', 'executing', 'normal', 'supervised', 'single_agent', "
                "'{}'::jsonb, false, 0)"
            ),
            {"g": gid, "t": tid},
        )
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k"), gid


async def test_reconnect_to_other_replica_with_last_event_id_has_no_gap_or_duplicate(
    db: Any, redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis.asyncio as aioredis

    import app.services.goal_service as gs_mod

    ctx, gid = await _goal_row(db)
    redis = aioredis.from_url(redis_url, decode_responses=True)

    owner = GoalService(db_session_factory=db, event_store=EventStore(db))
    owner._redis = redis
    owner._goals[gid] = GoalRecord(
        goal_id=gid,
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id=ctx.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    other = GoalService(db_session_factory=db, event_store=EventStore(db))
    other._redis_url_for_pubsub = redis_url

    # The owner emits 150 events; the client saw up to seq 3 there, then
    # reconnects to `other` (history to replay: 147 events, more than the old
    # 100-event cap, read in pages of 50 here).
    for i in range(1, 151):
        await owner._dispatch_event(gid, {"type": "step_started", "i": i}, tenant_ctx=ctx)

    real_page = other._list_events_since_persisted
    pages: list[int] = []

    async def _page(*a: Any, **k: Any) -> list[dict[str, Any]]:
        if not pages:
            # While the replay reads its first page the owner stores AND
            # publishes seq 151: it must arrive exactly once (it is in the
            # store and on the channel the stream subscribed to first).
            await owner._dispatch_event(gid, {"type": "step_started", "i": 151}, tenant_ctx=ctx)
        page = await real_page(*a, **k)
        pages.append(len(page))
        return page

    other._list_events_since_persisted = _page  # type: ignore[method-assign]
    monkeypatch.setattr(gs_mod, "_REPLAY_PAGE_SIZE", 50, raising=False)
    try:

        async def _collect() -> list[dict[str, Any]]:
            return [
                e
                async for e in other.subscribe_events(gid, ctx, since_sequence=3)
                if e["type"] != "_sse_heartbeat"
            ]

        task = asyncio.create_task(_collect())
        await asyncio.sleep(1.0)
        for i in range(152, 155):
            await owner._dispatch_event(gid, {"type": "step_started", "i": i}, tenant_ctx=ctx)
        await owner._dispatch_event(gid, {"type": "worker_failed", "error": "x"}, tenant_ctx=ctx)
        events = await asyncio.wait_for(task, timeout=20)
    finally:
        await redis.aclose()

    seqs = [e["_seq"] for e in events]
    assert seqs == list(range(4, 156))  # no gap, no duplicate
    assert [e.get("i") for e in events[:-1]] == list(range(4, 155))
    assert events[-1]["type"] == "worker_failed"
    assert len(pages) > 2  # the history was keyset-paged
