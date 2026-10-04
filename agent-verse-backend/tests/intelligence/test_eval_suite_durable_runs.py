"""MEM-53: eval-suite runs are durable — leased per-task rows, non-blocking steps.

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
from app.intelligence.eval_suite_jobs import RunSettings, run_step, run_until_done
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


async def test_concurrent_steps_run_every_task_exactly_once_and_finalize_once() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 40)
    goals = FakeGoals(running_polls=2)
    completions: list[str] = []

    async def hook(_s: Any, run: dict[str, Any], _c: Any) -> None:
        completions.append(run["run_id"])

    # Duplicate step chains (redelivery, sweeper) are harmless.
    outs = await asyncio.gather(*(
        run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                       on_completed=hook, cfg=_cfg())
        for _ in range(3)
    ))
    assert {o["status"] for o in outs} == {"completed"}
    assert len(goals.submits) == 40
    assert len({s["goal"] for s in goals.submits}) == 40
    assert completions == [run_id]
    (run,) = await store.list_runs("s")
    assert run["status"] == "completed" and run["passed"] == 40 and run["pass_rate"] == 1.0
    # every golden goal is traceable to its run and task
    assert all(s["execution_context"]["eval_suite_run_id"] == run_id for s in goals.submits)


async def test_a_step_never_blocks_on_a_running_goal_and_bounds_inflight() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 6)
    goals = FakeGoals(running_polls=10_000)
    out = await run_step(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                         cfg=_cfg())
    assert out["status"] == "running"
    progress = await store.run_progress(run_id)
    assert progress["running"] == 4 and progress["pending"] == 2  # concurrency 4
    assert len(goals.submits) == 4


async def test_a_restarted_worker_polls_the_recorded_goal_and_never_resubmits() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 3)
    goals = FakeGoals(running_polls=3)
    # A step submits the first task's goal, records it — then the worker dies.
    claim = await store.claim_pending(run_id, "worker-a", 30)
    assert claim is not None
    sub = await goals.submit_goal(goal=claim["task"]["goal"], tenant_ctx=ctx)
    assert await store.mark_waiting(run_id, claim["task_id"], "worker-a", sub["goal_id"], 600)
    # Another step died between claiming a task and submitting it: lease expires.
    claim2 = await store.claim_pending(run_id, "worker-a", 30)
    assert claim2 is not None
    for row in _MEM_TASKS[(ctx.tenant_id, run_id)]:
        if row["task_id"] == claim2["task_id"]:
            row["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    out = await run_until_done(store=store, run_id=run_id, goal_service=goals,
                               tenant_ctx=ctx, cfg=_cfg())
    assert out["status"] == "completed"
    assert len(goals.submits) == 3  # the recorded goal was polled, not resubmitted
    tasks = {t["task_id"]: t for t in await store.list_run_tasks(run_id)}
    assert tasks[claim["task_id"]]["goal_id"] == sub["goal_id"]
    assert tasks[claim["task_id"]]["passed"]
    assert tasks[claim2["task_id"]]["attempts"] == 2 and tasks[claim2["task_id"]]["passed"]


async def test_a_step_that_died_while_submitting_does_not_hold_the_only_slot() -> None:
    """With concurrency 1, a dead step's expired ``submitting`` row must not count as
    in flight forever — it is the very row the next step has to re-claim."""
    ctx = _ctx()
    store = EvalSuiteStore(None, ctx.tenant_id)
    await store.create("s", name="s", description="")
    await store.import_tasks("s", [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(2)],
                             replace=False)
    run_id = uuid.uuid4().hex
    await store.start_run("s", run_id, dataset_version=1, enqueue=True, concurrency=1)
    claim = await store.claim_pending(run_id, "dead-step", 30)
    assert claim is not None
    for row in _MEM_TASKS[(ctx.tenant_id, run_id)]:
        if row["task_id"] == claim["task_id"]:
            row["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    goals = FakeGoals()
    out = await run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                               cfg=_cfg(), max_steps=50)
    assert out["status"] == "completed"
    assert len(goals.submits) == 2


async def test_a_task_whose_workers_keep_dying_gives_up_unscored() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 1)
    for row in _MEM_TASKS[(ctx.tenant_id, run_id)]:
        row["attempts"] = 3  # three steps already died on it
    out = await run_until_done(store=store, run_id=run_id, goal_service=FakeGoals(),
                               tenant_ctx=ctx, cfg=_cfg())
    assert out["status"] == "completed"
    (task,) = await store.list_run_tasks(run_id)
    assert task["status"] == "error" and not task["passed"]
    assert "gave up after 3 attempts" in task["failure_reasons"][0]


async def test_failed_and_timed_out_goals_fail_their_tasks() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 2)
    goals = FakeGoals(outcome=lambda goal, _a: "failed" if goal == "goal 0" else "complete")
    await run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                         cfg=_cfg())
    by_goal = {t["goal"]: t for t in await store.list_run_tasks(run_id)}
    assert not by_goal["goal 0"]["passed"]
    assert by_goal["goal 0"]["terminal_event"] == "goal_failed"
    assert by_goal["goal 1"]["passed"]

    ctx2 = _ctx()
    store2, run2 = await _seed(ctx2, 1, max_iterations=1)
    slow = FakeGoals(running_polls=10_000)
    cfg = _cfg()
    cfg.timeout_max = 0.05  # a task's goal may run at most 50 ms here
    await run_until_done(store=store2, run_id=run2, goal_service=slow, tenant_ctx=ctx2, cfg=cfg)
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
                          agent_config_hash=agent_config_hash(agent), concurrency=1)
    goals = FakeGoals()
    loads = {"n": 0}

    async def loader(agent_id: str) -> dict[str, Any]:
        loads["n"] += 1
        return agent if loads["n"] == 1 else {**agent, "system_prompt": "v2"}

    await run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                         agent_loader=loader, cfg=_cfg())
    assert [s["agent_id"] for s in goals.submits] == ["a1"]
    tasks = await store.list_run_tasks(run_id)
    assert sum(t["passed"] for t in tasks) == 1
    assert sum("configuration changed" in " ".join(t["failure_reasons"]) for t in tasks) == 2


async def test_the_lease_fences_results_and_finalization_happens_once() -> None:
    ctx = _ctx()
    store, run_id = await _seed(ctx, 1)
    claim = await store.claim_pending(run_id, "owner-1", 30)
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


async def test_api_run_is_executed_in_process_with_progress(monkeypatch: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "eval_suite_goal_poll_seconds", 0.01)
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
        assert body["concurrency"] == 4
        for _ in range(300):
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
    cfg = _cfg()
    cfg.concurrency = 32
    await asyncio.gather(*(
        run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx, cfg=cfg)
        for _ in range(2)
    ))
    assert len(goals.submits) == n
    assert (await store.run_progress(run_id))["done"] == n


@pytest.mark.parametrize(("status", "rescheduled"), [("running", True), ("completed", False),
                                                     ("missing", False)])
def test_the_celery_step_reschedules_itself_only_while_running(
    monkeypatch: Any, status: str, rescheduled: bool
) -> None:
    from app.scaling import tasks as scaling_tasks

    async def _step(*_a: Any) -> dict[str, Any]:
        return {"status": status}

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(scaling_tasks, "_run_eval_suite_worker_async", _step)
    monkeypatch.setattr(scaling_tasks.run_eval_suite_worker, "apply_async",
                        lambda **kw: sent.append(kw))
    out = scaling_tasks.run_eval_suite_worker.run("t1", "free", "r1", 0)
    assert out["status"] == status
    assert bool(sent) is rescheduled
    if rescheduled:
        assert sent[0]["args"] == ["t1", "free", "r1", 0]
        assert sent[0]["queue"] == "maintenance" and sent[0]["countdown"] > 0
