"""MEM-53: eval-suite runs are durable — leased per-task rows, resumable, heartbeated.

A run was one asyncio task on the API replica with results written only at the
end: a restart lost it, large datasets could not complete, and a long run was
reported "abandoned" while still executing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.intelligence.eval_suite import GoldenTaskResult
from app.intelligence.eval_suite_jobs import RunSettings, run_suite_worker
from app.intelligence.eval_suite_store import _MEM_RUNS, _MEM_TASKS, EvalSuiteStore
from app.tenancy.context import PlanTier, TenantContext
from tests.intelligence._eval_fakes import FakeGoals, FastSettings


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=f"durable-{uuid.uuid4().hex[:8]}", plan=PlanTier.PROFESSIONAL, api_key_id="k"
    )


async def _seed(ctx: TenantContext, n: int, **task: Any) -> tuple[EvalSuiteStore, str]:
    store = EvalSuiteStore(None, ctx.tenant_id)
    await store.create("s", name="s", description="")
    await store.import_tasks(
        "s", [{"goal": f"goal {i}", "expected_tools": ["t"], **task} for i in range(n)],
        replace=False,
    )
    run_id = uuid.uuid4().hex
    total = await store.start_run("s", run_id, dataset_version=1, enqueue=True)
    assert total == n
    return store, run_id


def _cfg(**over: Any) -> RunSettings:
    s = FastSettings()
    for k, v in over.items():
        setattr(s, k, v)
    return RunSettings(s)


async def test_concurrent_workers_run_every_task_exactly_once_and_finalize_once() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 40)
    goals = FakeGoals(running_polls=2)
    completions: list[str] = []

    async def hook(_s: Any, run: dict[str, Any], _c: Any) -> None:
        completions.append(run["run_id"])

    outs = await asyncio.gather(*(
        run_suite_worker(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                         on_completed=hook, cfg=_cfg())
        for _ in range(6)
    ))
    assert sum(o["processed"] for o in outs) == 40
    assert len(goals.submits) == 40
    assert len({s["goal"] for s in goals.submits}) == 40
    assert completions == [run_id]
    (run,) = await store.list_runs("s")
    assert run["status"] == "completed" and run["passed"] == 40 and run["pass_rate"] == 1.0
    # every golden goal is traceable to its run and task
    assert all(s["execution_context"]["eval_suite_run_id"] == run_id for s in goals.submits)


async def test_a_redelivered_worker_resumes_the_same_goal_and_never_resubmits() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 3)
    goals = FakeGoals()
    # Worker A claims the first task, submits its goal, records it — and dies.
    claim = await store.claim_next(run_id, "worker-a", 30)
    assert claim is not None
    sub = await goals.submit_goal(goal=claim["task"]["goal"], tenant_ctx=ctx)
    assert await store.set_task_goal(run_id, claim["task_id"], "worker-a", sub["goal_id"])
    # Its lease expires (no renewals from a dead worker).
    for row in _MEM_TASKS[(ctx.tenant_id, run_id)]:
        if row["task_id"] == claim["task_id"]:
            row["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    out = await run_suite_worker(store=store, run_id=run_id, goal_service=goals,
                                 tenant_ctx=ctx, cfg=_cfg())
    assert out["status"] == "completed" and out["processed"] == 3
    assert len(goals.submits) == 3  # 1 by the dead worker + 2 new; the first was resumed
    tasks = await store.list_run_tasks(run_id)
    first = next(t for t in tasks if t["task_id"] == claim["task_id"])
    assert first["goal_id"] == sub["goal_id"] and first["attempts"] == 2 and first["passed"]


async def test_a_task_whose_workers_keep_dying_gives_up_unscored() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 1)
    for row in _MEM_TASKS[(ctx.tenant_id, run_id)]:
        row["attempts"] = 3  # three workers already died on it
    out = await run_suite_worker(store=store, run_id=run_id, goal_service=FakeGoals(),
                                 tenant_ctx=ctx, cfg=_cfg())
    assert out["status"] == "completed"
    (task,) = await store.list_run_tasks(run_id)
    assert task["status"] == "error" and not task["passed"]
    assert "gave up after 3 attempts" in task["failure_reasons"][0]


async def test_failed_and_timed_out_goals_fail_their_tasks() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 2)
    goals = FakeGoals(outcome=lambda goal, _a: "failed" if goal == "goal 0" else "complete",
                      running_polls=0)
    await run_suite_worker(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                           cfg=_cfg())
    by_goal = {t["goal"]: t for t in await store.list_run_tasks(run_id)}
    assert not by_goal["goal 0"]["passed"]
    assert by_goal["goal 0"]["terminal_event"] == "goal_failed"
    assert by_goal["goal 1"]["passed"]

    store2, run2 = await _seed(_ctx(), 1, max_iterations=1)
    slow = FakeGoals(running_polls=10_000)
    cfg = _cfg()
    cfg.timeout_max = 0.05  # a task's goal may run at most 50 ms here
    await run_suite_worker(store=store2, run_id=run2, goal_service=slow,
                           tenant_ctx=store2._tenant_id, cfg=cfg)
    (task,) = await store2.list_run_tasks(run2)
    assert task["status"] == "timeout" and slow.cancelled == [task["goal_id"]]


async def test_agent_pinning_stops_tasks_after_a_config_change() -> None:
    from app.intelligence.rollout_gate import agent_config_hash

    ctx = _ctx()
    store = EvalSuiteStore(None, ctx.tenant_id)
    await store.create("s", name="s", description="")
    await store.import_tasks("s", [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(3)],
                             replace=False)
    agent = {"agent_id": "a1", "system_prompt": "v1"}
    run_id = uuid.uuid4().hex
    await store.start_run("s", run_id, dataset_version=1, enqueue=True, agent_id="a1",
                          agent_config_hash=agent_config_hash(agent))
    goals = FakeGoals()
    loads = {"n": 0}

    async def loader(agent_id: str) -> dict[str, Any]:
        loads["n"] += 1
        return agent if loads["n"] == 1 else {**agent, "system_prompt": "v2"}

    await run_suite_worker(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                           agent_loader=loader, cfg=_cfg())
    assert [s["agent_id"] for s in goals.submits] == ["a1"]
    tasks = await store.list_run_tasks(run_id)
    assert sum(t["passed"] for t in tasks) == 1
    assert sum("configuration changed" in " ".join(t["failure_reasons"]) for t in tasks) == 2


async def test_the_lease_fences_results_and_finalization_happens_once() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 1)
    claim = await store.claim_next(run_id, "owner-1", 30)
    assert claim is not None
    stray = GoldenTaskResult(task_id=claim["task_id"], goal="g", passed=True)
    assert await store.record_task_result(run_id, "someone-else", stray) is False
    assert await store.record_task_result(run_id, "owner-1", stray) is True
    assert await store.finalize_run(run_id) is not None
    assert await store.finalize_run(run_id) is None


async def test_abandoned_is_derived_from_the_heartbeat_not_the_run_age() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 2)
    run = _MEM_RUNS[ctx.tenant_id]["s"][run_id]
    run["run_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()  # a very long run
    run["last_progress_at"] = datetime.now(UTC).isoformat()  # ...still heartbeating
    (listed,) = await store.list_runs("s")
    assert listed["status"] == "running"
    assert listed["progress"] == {"total": 2, "done": 0, "passed": 0, "failed": 0,
                                  "unscored": 0, "running": 0, "pending": 2}
    run["last_progress_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    (listed,) = await store.list_runs("s")
    assert listed["status"] == "abandoned"


async def test_api_run_is_executed_by_in_process_workers_with_progress() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    goals = FakeGoals()
    app.state.goal_service = goals
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        key = (await c.post("/tenants/signup", json={"name": "T", "email": "d@t.com"})).json()
        c.headers["X-API-Key"] = key["api_key"]
        sid = (await c.post("/intelligence/eval-suites", json={})).json()["suite_id"]
        await c.post(f"/intelligence/eval-suites/{sid}/import", json={
            "tasks": [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(7)]})
        r = await c.post(f"/intelligence/eval-suites/{sid}/run")
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["total"] == 7 and body["executor"] == "in_process"
        assert body["workers"] == 4
        for _ in range(200):
            runs = (await c.get(f"/intelligence/eval-suites/{sid}/results")).json()
            if runs[0]["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        assert runs[0]["status"] == "completed" and runs[0]["passed"] == 7
        page = (await c.get(f"/intelligence/eval-suites/{sid}/runs/{body['run_id']}/tasks",
                            params={"limit": 3})).json()
        assert page["progress"]["done"] == 7 and len(page["tasks"]) == 3


async def test_api_refuses_to_run_an_empty_suite() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        key = (await c.post("/tenants/signup", json={"name": "T", "email": "e@t.com"})).json()
        c.headers["X-API-Key"] = key["api_key"]
        sid = (await c.post("/intelligence/eval-suites", json={})).json()["suite_id"]
        assert (await c.post(f"/intelligence/eval-suites/{sid}/run")).status_code == 422


@pytest.mark.parametrize("n", [1500])
async def test_large_runs_complete(n: int) -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, n)
    goals = FakeGoals()
    await asyncio.gather(*(
        run_suite_worker(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                         cfg=_cfg())
        for _ in range(8)
    ))
    assert len(goals.submits) == n
    assert (await store.run_progress(run_id))["done"] == n
