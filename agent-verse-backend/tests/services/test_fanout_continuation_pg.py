"""a01-F006-05 / a01-F007-01 on real Postgres + Redis: fan-out continuations.

* the last sub-goal to finish re-queues its parked parent EXACTLY once, however
  many sub-goals finish at the same moment (conditional UPDATE);
* a parent never wakes while a sub-goal runs, and a failed re-enqueue parks it
  again for the sweeper;
* the sweeper cancels a sub-goal past its per-child timeout (ledger row failed,
  goal cancelled + Redis cancel flag, event appended) and wakes its parent; a
  sub-goal waiting for a human gets its deadline moved out instead;
* a supervisor parent crashed mid-dispatch resumes without duplicate sub-goals,
  parks, is woken once, and its re-entry aggregates the real results in order;
* cancelling a parent cancels its running sub-goals.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.fanout_ledger import FANOUT_TASK_KEY, ledger_for
from app.agent.supervisor import SUBGOAL_MARKER, SupervisorAgent
from app.providers.base import CompletionResponse
from app.services.event_store import EventStore
from app.services.fanout_continuation import (
    park_parent,
    sweep_fanout,
    wake_parent,
    wake_parent_of,
)
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, pool_size=12, max_overflow=4)
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
                "VALUES (:id, 'F', :e, 'professional', true)"
            ),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return tid


async def _goal(
    db: Any, tenant: str, *, status: str, parent: str | None = None, ctx: dict | None = None
) -> str:
    gid = uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, parent_goal_id, goal_text, status, priority, "
                "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                "VALUES (:id, :t, :p, 'research', :st, 'normal', 'bounded-autonomous', "
                "'single_agent', CAST(:ctx AS json), false, 0)"
            ),
            {"id": gid, "t": tenant, "p": parent, "st": status, "ctx": json.dumps(ctx or {})},
        )
    return gid


async def _set_status(db: Any, gid: str, status: str) -> None:
    async with db() as s, s.begin():
        await s.execute(text("UPDATE goals SET status = :s WHERE id = :g"), {"s": status, "g": gid})


async def _row(db: Any, gid: str) -> dict[str, Any]:
    async with db() as s:
        r = (
            await s.execute(
                text(
                    "SELECT status, error_message, execution_context::jsonb FROM goals "
                    "WHERE id = :g"
                ),
                {"g": gid},
            )
        ).one()
    return {"status": r[0], "error": r[1], "context": r[2]}


async def _parked_parent(db: Any, tenant: str, children: int) -> tuple[str, list[str]]:
    parent = await _goal(db, tenant, status="executing")
    kids = [await _goal(db, tenant, status="executing", parent=parent,
                        ctx={SUBGOAL_MARKER: parent}) for _ in range(children)]
    assert await park_parent(db, tenant_id=tenant, goal_id=parent, kind="supervisor",
                             plan="professional", connector_ids=["c1"]) == "parked"
    return parent, kids


class _Enqueued:
    def __init__(self, *, fail: bool = False) -> None:
        self.goals: list[dict[str, Any]] = []
        self.fail = fail

    def __call__(self, goal: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.goals.append(goal)


async def test_last_child_requeues_the_parent_exactly_once_under_concurrency(db: Any) -> None:
    tenant = await _tenant(db)
    for _round in range(5):
        parent, kids = await _parked_parent(db, tenant, children=6)
        assert (await _row(db, parent))["status"] == "waiting_children"
        enqueued = _Enqueued()
        gate = asyncio.Event()

        async def _child_finishes(child: str, _enq: _Enqueued = enqueued,
                                  _gate: asyncio.Event = gate) -> bool:
            await _gate.wait()
            await _set_status(db, child, "complete")
            return await wake_parent_of(db, tenant_id=tenant, child_goal_id=child, enqueue=_enq)

        tasks = [asyncio.create_task(_child_finishes(k)) for k in kids]
        # Extra concurrent wakes (the sweeper, the parker's own re-check).
        tasks += [
            asyncio.create_task(
                wake_parent(db, tenant_id=tenant, parent_goal_id=parent, enqueue=enqueued)
            )
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks)

        assert sum(1 for r in results if r) == 1
        assert [g["goal_id"] for g in enqueued.goals] == [parent]
        (goal,) = enqueued.goals
        assert goal["plan"] == "professional" and goal["connector_ids"] == ["c1"]
        row = await _row(db, parent)
        assert row["status"] == "planning"
        assert row["context"]["_fanout_wakes"] == 1


async def test_no_wake_while_a_child_runs_and_a_failed_enqueue_parks_again(db: Any) -> None:
    tenant = await _tenant(db)
    parent, (a, b) = await _parked_parent(db, tenant, children=2)
    enqueued = _Enqueued()
    await _set_status(db, a, "complete")
    assert not await wake_parent_of(db, tenant_id=tenant, child_goal_id=a, enqueue=enqueued)
    assert enqueued.goals == [] and (await _row(db, parent))["status"] == "waiting_children"

    await _set_status(db, b, "failed")
    broken = _Enqueued(fail=True)
    assert not await wake_parent_of(db, tenant_id=tenant, child_goal_id=b, enqueue=broken)
    assert (await _row(db, parent))["status"] == "waiting_children"  # parked for the sweeper
    assert await wake_parent(db, tenant_id=tenant, parent_goal_id=parent, enqueue=enqueued)
    assert len(enqueued.goals) == 1


async def test_park_never_overwrites_a_cancel_and_tenants_are_isolated(db: Any) -> None:
    tenant, other = await _tenant(db), await _tenant(db)
    parent = await _goal(db, tenant, status="cancelled")
    assert await park_parent(db, tenant_id=tenant, goal_id=parent, kind="goal_tree",
                             plan="free") == "cancelled"
    parked, (kid,) = await _parked_parent(db, tenant, children=1)
    await _set_status(db, kid, "complete")
    enqueued = _Enqueued()
    # Another tenant can never wake (or even see) this parent.
    assert not await wake_parent(db, tenant_id=other, parent_goal_id=parked, enqueue=enqueued)
    assert not await wake_parent_of(db, tenant_id=other, child_goal_id=kid, enqueue=enqueued)
    assert enqueued.goals == []


async def _ledger_row(db: Any, tenant: str, parent: str, key: str, child: str, *,
                      overdue: bool, timeout_s: int = 45) -> None:
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goal_fanout_ledger (tenant_id, parent_goal_id, kind, task_key, "
                "position, spec, child_goal_id, status, timeout_s, deadline_at) VALUES "
                "(:t, :p, 'supervisor', :k, 0, '{}'::jsonb, :c, 'dispatched', :to, "
                "now() + make_interval(secs => CAST(:d AS double precision)))"
            ),
            {"t": tenant, "p": parent, "k": key, "c": child, "to": timeout_s,
             "d": -5 if overdue else 3600},
        )


async def _ledger(db: Any, parent: str) -> dict[str, dict[str, Any]]:
    async with db() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT task_key, status, error, deadline_at > now() FROM goal_fanout_ledger "
                    "WHERE parent_goal_id = :p"
                ),
                {"p": parent},
            )
        ).all()
    return {r[0]: {"status": r[1], "error": r[2], "future_deadline": r[3]} for r in rows}


async def test_sweeper_times_out_an_overdue_child_and_wakes_its_parent(
    db: Any, redis_client: Any
) -> None:
    tenant = await _tenant(db)
    parent, (slow, approval, fast) = await _parked_parent(db, tenant, children=3)
    await _set_status(db, approval, "waiting_human")
    await _ledger_row(db, tenant, parent, "slow", slow, overdue=True)
    await _ledger_row(db, tenant, parent, "approval", approval, overdue=True)
    await _ledger_row(db, tenant, parent, "fast", fast, overdue=False)
    released: list[str] = []
    published: list[tuple[str, str]] = []
    enqueued = _Enqueued()

    async def _release(tenant_id: str, goal_id: str, ctx: dict[str, Any]) -> None:
        released.append(goal_id)

    def _publish(tenant_id: str, goal_id: str, event: dict[str, Any]) -> None:
        published.append((goal_id, str(event.get("type"))))

    out = await sweep_fanout(db, redis_client=redis_client, enqueue=enqueued,
                             release_slot=_release, publish=_publish)

    assert slow in out["children_timed_out"] and slow in out["children_cancelled"]
    slow_row = await _row(db, slow)
    assert slow_row["status"] == "cancelled" and "timed out after 45s" in slow_row["error"]
    assert redis_client.get(f"goal_cancelled:{slow}") == "1"
    assert (slow, "goal_cancelled") in published and released == [slow]
    rows = await _ledger(db, parent)
    assert rows["slow"]["status"] == "failed" and rows["slow"]["error"] == "Timeout after 45s"
    # Waiting for a human: not timed out, deadline moved out.
    assert (await _row(db, approval))["status"] == "waiting_human"
    assert rows["approval"]["status"] == "dispatched" and rows["approval"]["future_deadline"]
    assert rows["fast"]["status"] == "dispatched"
    # Two children still run: the parent stays parked. (The sweep is cross-tenant:
    # other tests' parked parents with nothing left to wait on are woken too.)
    assert parent not in [g["goal_id"] for g in enqueued.goals]
    assert (await _row(db, parent))["status"] == "waiting_children"

    # Both remaining children end: the next sweep re-queues the parent once.
    await _set_status(db, approval, "complete")
    await _set_status(db, fast, "complete")
    await sweep_fanout(db, redis_client=redis_client, enqueue=enqueued)
    await sweep_fanout(db, redis_client=redis_client, enqueue=enqueued)
    assert [g["goal_id"] for g in enqueued.goals if g["goal_id"] == parent] == [parent]


class _Planner:
    async def complete(self, request: Any) -> CompletionResponse:
        prompt = " ".join(str(m.content) for m in request.messages)
        if "decomposer" in prompt:
            content = '{"sub_tasks": [{"goal": "research A"}, {"goal": "research B"}]}'
        else:
            content = "synthesized: " + prompt
        return CompletionResponse(content=content, model="m", input_tokens=1, output_tokens=1)


class _RowGoalService:
    """submit_goal inserts a real child goals row (status planning), like GoalService."""

    def __init__(self, db: Any, tenant: str, *, crash_after: int | None = None) -> None:
        self.db, self.tenant, self.crash_after = db, tenant, crash_after
        self.submitted: list[str] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        ctx = kwargs["execution_context"]
        gid = await _goal(self.db, self.tenant, status="planning",
                          parent=ctx[SUBGOAL_MARKER], ctx=ctx)
        self.submitted.append(gid)
        if self.crash_after is not None and len(self.submitted) >= self.crash_after:
            raise asyncio.CancelledError  # the worker dies right after the INSERT
        return {"goal_id": gid}

    def subscribe_events(self, **_: Any) -> Any:
        raise AssertionError("a parked parent never streams a sub-goal")


async def test_supervisor_crash_resume_park_wake_and_aggregate(db: Any) -> None:
    tenant = await _tenant(db)
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    parent = await _goal(db, tenant, status="executing")

    def _sup(svc: Any) -> SupervisorAgent:
        return SupervisorAgent(planner_provider=_Planner(), goal_service=svc,
                               continuation=True, child_timeout_seconds=120)

    def _ledger_obj() -> Any:
        return ledger_for(db, tenant_id=tenant, parent_goal_id=parent, kind="supervisor")

    crashed = _RowGoalService(db, tenant, crash_after=1)
    with pytest.raises(asyncio.CancelledError):
        await _sup(crashed).run(goal="research", tenant_ctx=ctx, parent_goal_id=parent,
                                ledger=_ledger_obj())

    redelivered = _RowGoalService(db, tenant)
    first = await _sup(redelivered).run(goal="research", tenant_ctx=ctx,
                                        parent_goal_id=parent, ledger=_ledger_obj())
    assert first.parked
    assert len(crashed.submitted) + len(redelivered.submitted) == 2  # no duplicate child
    async with db() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT task_key, child_goal_id, timeout_s, deadline_at IS NOT NULL "
                    "FROM goal_fanout_ledger WHERE parent_goal_id = :p ORDER BY position"
                ),
                {"p": parent},
            )
        ).all()
        keys = (
            await s.execute(
                text("SELECT execution_context ->> :m FROM goals WHERE parent_goal_id = :p"),
                {"m": FANOUT_TASK_KEY, "p": parent},
            )
        ).all()
    assert len({k[0] for k in keys}) == 2
    assert all(r[1] and r[2] == 120 and r[3] for r in rows)

    assert await park_parent(db, tenant_id=tenant, goal_id=parent, kind="supervisor",
                             plan="professional") == "parked"
    store = EventStore(db)
    children = [r[1] for r in rows]
    for i, child in enumerate(reversed(children)):  # finish out of order
        label = "B" if i == 0 else "A"
        await store.append_event(
            child, {"type": "step_complete", "step": "s", "output": f"result {label}"},
            tenant_ctx=ctx,
        )
        await store.append_event(child, {"type": "goal_complete"}, tenant_ctx=ctx)
    enqueued = _Enqueued()
    for child in children:
        await _set_status(db, child, "complete")
    woke = await asyncio.gather(
        *(wake_parent_of(db, tenant_id=tenant, child_goal_id=c, enqueue=enqueued)
          for c in children)
    )
    assert sum(woke) == 1 and [g["goal_id"] for g in enqueued.goals] == [parent]

    reentry = _RowGoalService(db, tenant)
    result = await _sup(reentry).run(goal="research", tenant_ctx=ctx, parent_goal_id=parent,
                                     ledger=_ledger_obj())
    assert not result.parked and result.success
    assert reentry.submitted == []
    assert [t.goal for t in result.tasks] == ["research A", "research B"]
    assert [t.result for t in result.tasks] == ["result A", "result B"]
    entries = await _ledger_obj().load()
    assert [e.status for e in entries] == ["complete", "complete"]


async def test_cancelling_a_parent_cancels_its_running_sub_goals(db: Any) -> None:
    from app.services.goal_service import GoalService

    tenant = await _tenant(db)
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    parent, (running, done) = await _parked_parent(db, tenant, children=2)
    await _set_status(db, done, "complete")

    svc = GoalService(db_session_factory=db)
    out = await svc.cancel_goal(parent, ctx)

    assert out["status"] == "cancelled"
    assert out["cancelled_sub_goals"] == [running]
    assert (await _row(db, parent))["status"] == "cancelled"
    assert (await _row(db, running))["status"] == "cancelled"
    assert (await _row(db, done))["status"] == "complete"
    # The parent lists its sub-goals; a sub-goal names its parent.
    detail = await svc.get_goal(parent, ctx)
    assert {c["goal_id"] for c in detail["sub_goals"]} == {running, done}
    assert (await svc.get_goal(running, ctx))["parent_goal_id"] == parent
