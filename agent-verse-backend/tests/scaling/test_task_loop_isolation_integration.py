"""L-01: back-to-back Celery task loops never share a loop-bound DB/Redis client.

The live worker logged ~38 "RuntimeError: Event loop is closed" per 10 min and
"dispose_task_engine ... got Future attached to a different loop" (session.py,
the maintenance-engine dispose). Root cause: ``agentverse.coordination.
dispatch_outbox`` (beat, every few seconds) ran its body with ``asyncio.run``,
which closes the loop WITHOUT disposing the engines. Its pooled asyncpg
connections (application + maintenance engine) stayed pooled on a dead loop, so
the next task's first query (``fire_due_org_mission_schedules`` ->
``system_session``) or loop-end ``dispose_task_engine`` failed.

This runs real task entrypoints back to back, each on its own fresh loop,
against a real Postgres + Redis, with a separate maintenance engine like
production, and asserts no loop error surfaces anywhere: not as a task result,
not in the logs (SQLAlchemy's pool logs failed closes at ERROR), and not as an
unraisable exception from a finaliser (pytest turns those into errors).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/scaling/test_task_loop_isolation_integration.py -q -m integration
"""

from __future__ import annotations

import gc
import logging
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_LOOP_ERRORS = ("Event loop is closed", "attached to a different loop")


async def _system_and_app_queries(redis_url: str) -> dict[str, Any]:
    """A task body touching both engines and an async Redis client."""
    import redis.asyncio as aioredis
    from sqlalchemy import text

    from app.db.rls import system_session
    from app.db.session import get_session_factory, get_system_session_factory

    async with get_system_session_factory()() as s, s.begin(), system_session(s):
        system_one = (await s.execute(text("SELECT 1"))).scalar_one()
    async with get_session_factory()() as s:
        app_one = (await s.execute(text("SELECT 1"))).scalar_one()
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        pong = await client.ping()
    finally:
        await client.aclose()
    return {"system": system_one, "app": app_one, "redis": pong}


def _loop_error_records(records: list[logging.LogRecord]) -> list[str]:
    found: list[str] = []
    for record in records:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += f" {record.exc_info[1]!r}"
        if any(marker in text for marker in _LOOP_ERRORS):
            found.append(f"{record.name}: {text[:300]}")
    return found


def test_back_to_back_task_loops_raise_no_loop_errors(
    test_backends: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pg_url, redis_url = test_backends
    # A distinct maintenance engine, as in production (MAINTENANCE_DATABASE_URL).
    monkeypatch.setenv("MAINTENANCE_DATABASE_URL", pg_url)
    from tests._test_backends import reset_db_singletons

    reset_db_singletons()

    from app.coordination.outbox_tasks import dispatch_coordination_outbox
    from app.db.session import run_in_fresh_loop

    caplog.set_level(logging.WARNING)
    results: list[Any] = []
    for _ in range(3):
        results.append(dispatch_coordination_outbox())
        results.append(run_in_fresh_loop(_system_and_app_queries(redis_url)))
    gc.collect()  # finalise anything a closed loop left behind

    outbox_results = results[0::2]
    query_results = results[1::2]
    assert all(r.get("status") == "ok" for r in outbox_results), outbox_results
    assert all(r == {"system": 1, "app": 1, "redis": True} for r in query_results)
    assert _loop_error_records(caplog.records) == []
