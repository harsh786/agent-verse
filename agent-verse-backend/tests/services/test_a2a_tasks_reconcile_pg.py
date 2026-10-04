"""A2A-01 on Postgres: task outcomes and callbacks are driven from the database.

The goal used to run from an untracked asyncio task after the 202; a restart
stranded the task as ``accepted`` with no goal or callback. Now the reconciler
(beat, any replica) finalises open tasks from their goals, fails tasks whose goal
never started or vanished, and claims callbacks with backoff until a 2xx.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.a2a_tasks import (
    CALLBACK_MAX_ATTEMPTS,
    claim_due_callbacks,
    deliver_callback,
    finalize_open_tasks,
    reconcile_task,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, pool_size=5)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _tenant(db: Any) -> str:
    tid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, 'A', :e, 'starter', true)"
            ),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return tid


async def _goal(db: Any, tenant: str, status: str, error: str = "") -> str:
    gid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, autonomy_mode, "
                "workflow_mode, execution_context, dry_run, iterations, error_message) "
                "VALUES (:id, :t, 'g', :st, 'normal', 'bounded-autonomous', 'single_agent', "
                "CAST('{}' AS json), false, 0, :err)"
            ),
            {"id": gid, "t": tenant, "st": status, "err": error},
        )
    return gid


async def _task(
    db: Any,
    tenant: str,
    *,
    goal_id: str | None,
    status: str = "working",
    callback: str = "",
    age_s: int = 0,
) -> str:
    tid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO a2a_tasks (id, tenant_id, goal_text, status, callback_url, "
                "goal_id, created_at) VALUES (:id, :t, 'g', :st, :cb, :gid, "
                "now() - make_interval(secs => :age))"
            ),
            {"id": tid, "t": tenant, "st": status, "cb": callback, "gid": goal_id, "age": age_s},
        )
    return tid


async def _row(db: Any, task_id: str) -> dict[str, Any]:
    async with db() as s:
        r = (
            await s.execute(
                text(
                    "SELECT status, result, callback_attempts, callback_delivered_at "
                    "FROM a2a_tasks WHERE id = :id"
                ),
                {"id": task_id},
            )
        ).one()
    return {"status": r[0], "result": r[1], "attempts": r[2], "delivered": r[3] is not None}


async def _next_at(db: Any, task_id: str) -> Any:
    async with db() as s:
        return (
            await s.execute(
                text("SELECT callback_next_at FROM a2a_tasks WHERE id = :id"), {"id": task_id}
            )
        ).scalar_one()


async def _text(tenant_id: str, goal_id: str) -> str:
    return f"answer for {goal_id}"


async def test_open_tasks_are_finalised_from_their_goals(db: Any) -> None:
    t = await _tenant(db)
    g_ok = await _goal(db, t, "complete")
    g_bad = await _goal(db, t, "failed", "tool exploded")
    g_run = await _goal(db, t, "executing")
    done = await _task(db, t, goal_id=g_ok)
    failed = await _task(db, t, goal_id=g_bad)
    running = await _task(db, t, goal_id=g_run)
    stranded = await _task(db, t, goal_id=None, status="accepted", age_s=3600)
    fresh = await _task(db, t, goal_id=None, status="accepted")
    orphan = await _task(db, t, goal_id=uuid.uuid4().hex, age_s=3600)
    young_orphan = await _task(db, t, goal_id=uuid.uuid4().hex)  # goal row may lag

    out = await finalize_open_tasks(db, result_text=_text)
    assert {done, failed} <= set(out["finalized"])
    assert {stranded, orphan} <= set(out["errored"])
    assert (await _row(db, done))["status"] == "complete"
    assert (await _row(db, done))["result"] == f"answer for {g_ok}"
    assert (await _row(db, failed)) | {} == {
        "status": "failed",
        "result": "tool exploded",
        "attempts": 0,
        "delivered": False,
    }
    assert (await _row(db, running))["status"] == "working"
    assert (await _row(db, stranded))["status"] == "error"
    assert (await _row(db, fresh))["status"] == "accepted"  # still within its grace period
    assert (await _row(db, orphan))["status"] == "error"
    assert (await _row(db, young_orphan))["status"] == "working"

    again = await finalize_open_tasks(db, result_text=_text)
    assert done not in again["finalized"] and failed not in again["finalized"]


async def test_concurrent_reconcilers_finalise_each_task_once(db: Any) -> None:
    t = await _tenant(db)
    tasks = [await _task(db, t, goal_id=await _goal(db, t, "complete")) for _ in range(6)]
    outs = await asyncio.gather(*[finalize_open_tasks(db, result_text=_text) for _ in range(4)])
    finalized = [tid for o in outs for tid in o["finalized"] if tid in tasks]
    assert sorted(finalized) == sorted(tasks)  # each exactly once across replicas


async def test_callbacks_are_claimed_retried_with_backoff_and_recorded(db: Any) -> None:
    t = await _tenant(db)
    task = await _task(
        db, t, goal_id=await _goal(db, t, "complete"), callback="https://r.example/cb"
    )
    await finalize_open_tasks(db, result_text=_text)

    claimed = await claim_due_callbacks(db)
    assert (task, t) in claimed
    assert (task, t) not in await claim_due_callbacks(db)  # backoff: not due again yet

    sent: list[tuple[str, str, str, str]] = []

    async def _fail(url: str, task_id: str, status: str, result: str) -> bool:
        sent.append((url, task_id, status, result))
        return False

    assert await deliver_callback(db, task, t, _fail) is False
    assert sent[0][:3] == ("https://r.example/cb", task, "complete")
    assert (await _row(db, task))["delivered"] is False

    async with db() as s, s.begin():  # the backoff elapses
        await s.execute(
            text("UPDATE a2a_tasks SET callback_next_at = now() WHERE id = :id"), {"id": task}
        )
    assert (task, t) in await claim_due_callbacks(db)

    async def _ok(url: str, task_id: str, status: str, result: str) -> bool:
        return True

    assert await deliver_callback(db, task, t, _ok) is True
    row = await _row(db, task)
    assert row["delivered"] is True and row["attempts"] == 2
    async with db() as s, s.begin():
        await s.execute(
            text("UPDATE a2a_tasks SET callback_next_at = now() WHERE id = :id"), {"id": task}
        )
    assert (task, t) not in await claim_due_callbacks(db)  # delivered: never again


async def test_concurrent_claimers_never_double_claim(db: Any) -> None:
    t = await _tenant(db)
    tasks = []
    for _ in range(10):
        tasks.append(
            await _task(
                db, t, goal_id=await _goal(db, t, "failed"), callback="https://r.example/cb"
            )
        )
    await finalize_open_tasks(db, result_text=_text)
    results = await asyncio.gather(*[claim_due_callbacks(db) for _ in range(4)])
    claimed = [tid for r in results for tid, _ in r if tid in tasks]
    assert sorted(claimed) == sorted(tasks)


async def test_reconcile_on_read_finalises_one_task_under_rls(db: Any) -> None:
    t = await _tenant(db)
    goal = await _goal(db, t, "executing")
    task = await _task(db, t, goal_id=goal)
    assert await reconcile_task(db, task_id=task, tenant_id=t, result_text=_text) is False
    async with db() as s, s.begin():
        await s.execute(text("UPDATE goals SET status = 'complete' WHERE id = :g"), {"g": goal})
    other = await _tenant(db)
    assert await reconcile_task(db, task_id=task, tenant_id=other, result_text=_text) is False
    assert await reconcile_task(db, task_id=task, tenant_id=t, result_text=_text) is True
    assert (await _row(db, task))["status"] == "complete"
    assert await reconcile_task(db, task_id=task, tenant_id=t, result_text=_text) is False


async def test_no_callback_and_exhausted_callbacks_leave_the_due_index(db: Any) -> None:
    t = await _tenant(db)
    silent = await _task(db, t, goal_id=await _goal(db, t, "complete"))
    loud = await _task(
        db, t, goal_id=await _goal(db, t, "complete"), callback="https://r.example/cb"
    )
    await finalize_open_tasks(db, result_text=_text)
    assert await _next_at(db, silent) is None  # nothing to deliver: not in the due index
    for _ in range(CALLBACK_MAX_ATTEMPTS):
        async with db() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE a2a_tasks SET callback_next_at = now() "
                    "WHERE id = :id AND callback_next_at IS NOT NULL"
                ),
                {"id": loud},
            )
        await claim_due_callbacks(db)
    row = await _row(db, loud)
    assert row["attempts"] == CALLBACK_MAX_ATTEMPTS and row["delivered"] is False
    assert await _next_at(db, loud) is None  # given up: never claimed again
    assert (loud, t) not in await claim_due_callbacks(db)
