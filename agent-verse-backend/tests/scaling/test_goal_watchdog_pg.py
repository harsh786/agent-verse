"""GOAL-STALL: the durable runner heartbeat and the stale-runner reaper on real
Postgres + Redis.

Live evidence: a worker process running a goal died (SIGABRT), Celery redelivered
the task, the redelivery found the dead runner's Redis lock and skipped, and the
goal stayed ``executing`` with no events until it was cancelled 7 minutes later
(the only reaper waits out the plan's 1 h goal timeout).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.scaling.goal_watchdog import (
    GOAL_LOCK_PREFIX,
    GoalHeartbeat,
    reap_stale_goal_runners,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(scope="module")
def redis_client(redis_url: str) -> Any:
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    client.close()


async def _tenant(db: Any) -> str:
    tid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, 'W', :e, 'starter', true)"
            ),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return tid


async def _goal(
    db: Any,
    tenant: str,
    *,
    status: str = "executing",
    stale_for_s: float | None = 3600,
    token: str | None = "dead-run",
    context: str = "{}",
    events: tuple[str, ...] = (),
) -> str:
    gid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, autonomy_mode, "
                "workflow_mode, execution_context, dry_run, iterations, heartbeat_at, "
                "runner_token) VALUES (:id, :t, 'Summarize the report', :st, 'normal', "
                "'bounded-autonomous', 'single_agent', CAST(:ctx AS json), false, 0, "
                "CASE WHEN CAST(:stale AS float) IS NULL THEN NULL "
                "ELSE now() - make_interval(secs => CAST(:stale AS float)) END, :tok)"
            ),
            {"id": gid, "t": tenant, "st": status, "ctx": context, "stale": stale_for_s,
             "tok": token},
        )
        for i, etype in enumerate(events, start=1):
            await s.execute(
                text(
                    "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, "
                    "payload) VALUES (:id, :t, :g, :n, :e, '{}')"
                ),
                {"id": uuid.uuid4().hex, "t": tenant, "g": gid, "n": i, "e": etype},
            )
            await s.execute(text("UPDATE goals SET event_seq = :n WHERE id = :g"),
                            {"n": i, "g": gid})
    return gid


async def _row(db: Any, gid: str) -> dict[str, Any]:
    async with db() as s:
        r = (
            await s.execute(
                text(
                    "SELECT status, heartbeat_at, runner_token, error_message, "
                    "execution_context::jsonb FROM goals WHERE id = :g"
                ),
                {"g": gid},
            )
        ).one()
    return {"status": r[0], "heartbeat_at": r[1], "runner_token": r[2], "error": r[3],
            "context": r[4]}


async def _events(db: Any, gid: str) -> list[str]:
    async with db() as s:
        rows = await s.execute(
            text("SELECT event_type FROM goal_events WHERE goal_id = :g ORDER BY sequence"),
            {"g": gid},
        )
    return [r[0] for r in rows.fetchall()]


class _Recorder:
    def __init__(self) -> None:
        self.enqueued: list[str] = []
        self.released: list[str] = []
        self.published: list[tuple[str, str]] = []

    def enqueue(self, goal: dict[str, Any]) -> None:
        self.enqueued.append(goal["goal_id"])

    async def release(self, tenant_id: str, ctx: dict[str, Any]) -> None:
        self.released.append(tenant_id)

    def publish(self, tenant_id: str, goal_id: str, event: dict[str, Any]) -> None:
        self.published.append((goal_id, str(event.get("type"))))


async def _reap(db: Any, redis_client: Any, rec: _Recorder, **kw: Any) -> dict[str, Any]:
    return await reap_stale_goal_runners(
        db,
        redis_client=redis_client,
        enqueue=rec.enqueue,
        release_slot=rec.release,
        publish=rec.publish,
        stale_s=kw.pop("stale_s", 60),
        max_requeues=kw.pop("max_requeues", 1),
    )


async def _wait_for(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached")


# ── heartbeat ───────────────────────────────────────────────────────────────


async def test_heartbeat_takes_over_the_row_and_keeps_it_fresh(db: Any, pg_url: str) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, stale_for_s=3600, token="dead-run")
    beat = GoalHeartbeat(
        goal_id=gid, tenant_id=tenant, runner_token="live-run", database_url=pg_url,
        interval_s=0.2, loop_stall_s=60,
    ).start()
    try:
        async def _fresh() -> bool:
            row = await _row(db, gid)
            return row["runner_token"] == "live-run" and beat.beats >= 3

        await _wait_for(_fresh)
    finally:
        beat.stop()
    row = await _row(db, gid)
    async with db() as s:
        age = (await s.execute(text("SELECT now() - CAST(:t AS timestamptz)"),
                               {"t": row["heartbeat_at"]})).scalar()
    assert age.total_seconds() < 5


async def test_a_replaced_run_stops_beating(db: Any, pg_url: str) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, stale_for_s=None, token=None)
    old = GoalHeartbeat(
        goal_id=gid, tenant_id=tenant, runner_token="old-run", database_url=pg_url,
        interval_s=0.2, loop_stall_s=60,
    ).start()
    await _wait_for(lambda: _is(old.beats >= 1))
    new = GoalHeartbeat(
        goal_id=gid, tenant_id=tenant, runner_token="new-run", database_url=pg_url,
        interval_s=0.2, loop_stall_s=60,
    ).start()
    try:
        await _wait_for(lambda: _is(not old._thread.is_alive()))  # type: ignore[union-attr]
        assert (await _row(db, gid))["runner_token"] == "new-run"
    finally:
        new.stop()
        old.stop()


async def test_heartbeat_stops_when_the_goal_finishes(db: Any, pg_url: str) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, stale_for_s=None, token=None)
    beat = GoalHeartbeat(
        goal_id=gid, tenant_id=tenant, runner_token="r", database_url=pg_url,
        interval_s=0.2, loop_stall_s=60,
    ).start()
    await _wait_for(lambda: _is(beat.beats >= 1))
    async with db() as s, s.begin():
        await s.execute(text("UPDATE goals SET status = 'complete' WHERE id = :g"), {"g": gid})
    await _wait_for(lambda: _is(not beat._thread.is_alive()))  # type: ignore[union-attr]
    beat.stop()


async def test_heartbeat_is_withheld_when_the_goal_loop_stalls(db: Any, pg_url: str) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, stale_for_s=3600, token="r")
    beat = GoalHeartbeat(
        goal_id=gid, tenant_id=tenant, runner_token="r", database_url=pg_url,
        interval_s=0.1, loop_stall_s=0.01,
    )
    time.sleep(0.05)  # the loop never touched it: stalled before the first beat
    beat.start()
    await asyncio.sleep(0.6)
    beat.stop()
    assert beat.beats == 0
    async with db() as s:
        age = (await s.execute(text("SELECT now() - heartbeat_at FROM goals WHERE id = :g"),
                               {"g": gid})).scalar()
    assert age.total_seconds() > 3000  # still the stale value


async def _is(value: bool) -> bool:
    return value


# ── reaper ──────────────────────────────────────────────────────────────────


async def test_dead_runner_without_side_effects_is_requeued_once(
    db: Any, redis_client: Any
) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, token="dead-run", events=("step_started",))
    redis_client.set(f"{GOAL_LOCK_PREFIX}{gid}", "dead-run", px=3_600_000)
    rec = _Recorder()

    result = await _reap(db, redis_client, rec)

    assert gid in result["requeued"] and rec.enqueued.count(gid) == 1
    row = await _row(db, gid)
    assert row["status"] == "planning" and row["heartbeat_at"] is None
    assert row["context"].get("watchdog_requeues") == 1
    assert redis_client.get(f"{GOAL_LOCK_PREFIX}{gid}") is None  # the dead lock is gone
    assert "goal_runner_lost" in await _events(db, gid)
    assert rec.released == []  # the requeued run keeps the slot

    # It goes stale again (the new run died too): no second requeue, it fails.
    async with db() as s, s.begin():
        await s.execute(
            text("UPDATE goals SET status = 'executing', runner_token = 'run-2', "
                 "heartbeat_at = now() - interval '1 hour' WHERE id = :g"),
            {"g": gid},
        )
    result = await _reap(db, redis_client, rec)
    assert gid in result["failed"]
    row = await _row(db, gid)
    assert row["status"] == "failed" and "already requeued" in row["error"]
    assert rec.released == [tenant]


async def test_dead_runner_after_a_tool_ran_is_failed_not_rerun(
    db: Any, redis_client: Any
) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, events=("step_started", "tool_call_complete"))
    rec = _Recorder()

    result = await _reap(db, redis_client, rec)

    assert gid in result["failed"] and gid not in rec.enqueued
    row = await _row(db, gid)
    assert row["status"] == "failed"
    assert "Goal runner lost" in row["error"] and "already run a tool" in row["error"]
    events = await _events(db, gid)
    assert events[-2:] == ["goal_runner_lost", "goal_failed"]
    assert (gid, "goal_failed") in rec.published
    assert rec.released == [tenant]


async def test_live_and_finished_goals_are_left_alone(db: Any, redis_client: Any) -> None:
    tenant = await _tenant(db)
    live = await _goal(db, tenant, stale_for_s=5)
    never_beat = await _goal(db, tenant, stale_for_s=None, token=None)
    finished = await _goal(db, tenant, status="complete")
    waiting = await _goal(db, tenant, status="waiting_human")
    rec = _Recorder()

    result = await _reap(db, redis_client, rec)

    touched = set(result["requeued"]) | set(result["failed"])
    assert not touched & {live, never_beat, finished, waiting}
    assert (await _row(db, live))["status"] == "executing"


async def test_another_runs_lock_is_never_released(db: Any, redis_client: Any) -> None:
    tenant = await _tenant(db)
    gid = await _goal(db, tenant, token="dead-run")
    redis_client.set(f"{GOAL_LOCK_PREFIX}{gid}", "someone-else", px=3_600_000)
    await _reap(db, redis_client, _Recorder())
    assert redis_client.get(f"{GOAL_LOCK_PREFIX}{gid}") == "someone-else"


async def test_concurrent_reapers_handle_each_goal_once(db: Any, redis_client: Any) -> None:
    tenant = await _tenant(db)
    goals = [await _goal(db, tenant) for _ in range(5)]
    rec = _Recorder()

    results = await asyncio.gather(*(_reap(db, redis_client, rec) for _ in range(3)))

    handled = [g for r in results for g in r["requeued"] + r["failed"] if g in goals]
    assert sorted(handled) == sorted(goals)
    assert sorted(g for g in rec.enqueued if g in goals) == sorted(goals)
