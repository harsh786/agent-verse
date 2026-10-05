"""P7-1: an AI-Ops golden run never blocks a worker slot its own goals need.

Live EVAL-GOLDEN: one Celery worker with ``--concurrency=2`` served both the
``maintenance`` queue (the dataset run) and the goal queue. The run waited
inline 120 s per case on one slot; the other slot was held by an unrelated goal
parked on HITL; so no case goal ever got a slot and 9/10 cases were cancelled.

Here a simulated two-slot worker runs everything — the run's steps AND its
case goals — while one slot stays occupied by an unrelated blocked goal. The
run is advanced by short non-blocking steps that poll their goals, so a 5-case
suite completes on the single free slot.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.evals.ai_ops_jobs import MemoryRunLease, RunConfig, run_step
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-two-slot", plan=PlanTier.ENTERPRISE, api_key_id="k")
_TASKS = [{"input": f"q{i}", "expected_output": f"answer {i}"} for i in range(5)]


class Worker:
    """A Celery worker with N slots: every job (run step or goal) needs one."""

    def __init__(self, slots: int) -> None:
        self._slots = asyncio.Semaphore(slots)
        self._jobs: set[asyncio.Task[None]] = set()
        self.step_holds: list[float] = []

    def enqueue(self, job: Callable[[], Awaitable[None]], countdown: float = 0.0) -> None:
        async def _run() -> None:
            if countdown:
                await asyncio.sleep(countdown)
            async with self._slots:
                await job()

        task = asyncio.ensure_future(_run())
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def shutdown(self) -> None:
        for task in list(self._jobs):
            task.cancel()


class Store:
    """The durable store's run surface (lease + fenced save), in memory."""

    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    async def get_eval_result(self, tenant_id: str, result_id: str) -> dict[str, Any] | None:
        row = self.results.get(result_id)
        return copy.deepcopy(row) if row is not None else None

    async def claim_run(
        self, tenant_id: str, result_id: str, owner: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        row = MemoryRunLease.claim(self.results.get(result_id), owner, lease_seconds)
        return copy.deepcopy(row) if row is not None else None

    async def save_run(
        self, tenant_id: str, result_id: str, owner: str, payload: dict[str, Any],
        lease_seconds: float, *, release: bool = False,
    ) -> bool:
        if not MemoryRunLease.fenced(self.results.get(result_id), owner):
            return False
        stored = copy.deepcopy(payload)
        MemoryRunLease.stamp(stored, owner, lease_seconds, release)
        self.results[result_id] = stored
        return True

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        return {"dataset_id": dataset_id, "version": 1, "golden_tasks": _TASKS}

    async def get_judge(self, tenant_id: str, judge_id: str) -> dict[str, Any] | None:
        return None

    async def get_baseline(self, tenant_id: str, metric_name: str) -> float | None:
        return None

    async def add_alert(self, *, tenant_id: str, alert: dict[str, Any]) -> None:
        return None

    async def set_baseline_if_absent(
        self, *, tenant_id: str, metric_name: str, value: float
    ) -> None:
        return None


class GoalService:
    """Goals are queued on the worker and need a slot to execute."""

    def __init__(self, worker: Worker | None) -> None:
        self.worker = worker
        self.goals: dict[str, dict[str, Any]] = {}
        self.cancelled: list[str] = []

    async def submit_goal(self, *, goal: str, execution_context: dict[str, Any],
                          **_: Any) -> dict[str, Any]:
        gid = f"g-{execution_context['ai_ops_case_index']}"
        if gid in self.goals:  # dedup scope per (run, case)
            return {"goal_id": gid}
        self.goals[gid] = {"status": "planning", "events": [], "goal": goal}

        async def _execute() -> None:
            rec = self.goals[gid]
            if rec["status"] == "cancelled":
                return
            rec["status"] = "executing"
            await asyncio.sleep(0.02)
            n = goal[1:]
            rec["events"] = [
                {"type": "step_complete", "payload": {"output": f"answer {n}"}, "goal_id": gid},
                {"type": "goal_complete", "payload": {}, "goal_id": gid},
            ]
            rec["status"] = "complete"

        if self.worker is not None:
            self.worker.enqueue(_execute)
        return {"goal_id": gid}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {"goal_id": goal_id, "status": self.goals[goal_id]["status"]}

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        return list(self.goals[goal_id]["events"])

    async def cancel_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        self.cancelled.append(goal_id)
        self.goals[goal_id]["status"] = "cancelled"
        return {"goal_id": goal_id, "status": "cancelled"}


def _queued(store: Store) -> None:
    store.results["r1"] = {
        "result_id": "r1", "dataset_id": "d1", "tenant_id": _CTX.tenant_id,
        "agent_id": None, "status": "queued", "passed": False, "total_cases": len(_TASKS),
        "judge": None, "created_at": "2026-10-05T00:00:00+00:00",
    }


async def _drive(worker: Worker, store: Store, goals: GoalService, cfg: RunConfig) -> None:
    """The Celery chain: each step runs on a slot and re-enqueues the next one."""
    done = asyncio.Event()

    async def _step() -> None:
        t0 = time.monotonic()
        out = await run_step(store=store, tenant_ctx=_CTX, result_id="r1",
                             goal_service=goals, provider=None, cfg=cfg)
        worker.step_holds.append(time.monotonic() - t0)
        if out["status"] == "running":
            worker.enqueue(_step, countdown=cfg.poll_seconds)
        else:
            done.set()

    worker.enqueue(_step)
    await asyncio.wait_for(done.wait(), timeout=15)


async def test_five_case_suite_completes_on_a_two_slot_worker_with_one_slot_blocked() -> None:
    worker = Worker(slots=2)
    blocker_release = asyncio.Event()
    # An unrelated goal parked on HITL occupies one of the two slots throughout.
    worker.enqueue(blocker_release.wait)
    await asyncio.sleep(0)
    store, goals = Store(), GoalService(worker)
    _queued(store)
    cfg = RunConfig(concurrency=4, poll_seconds=0.01, lease_seconds=30, case_timeout=10)
    try:
        await _drive(worker, store, goals, cfg)
    finally:
        blocker_release.set()
        worker.shutdown()

    result = store.results["r1"]
    assert result["status"] == "completed"
    assert result["total_cases"] == 5
    assert result["executed_cases"] == 5 and result["scored_cases"] == 5
    assert [c["actual"] for c in result["cases"]] == [f"answer {i}" for i in range(5)]
    assert result["passed"] is True
    assert goals.cancelled == []
    assert "inflight" not in result and result["lease_until"] == 0.0
    # No step ever sat on its slot waiting for a goal.
    assert max(worker.step_holds) < 1.0


async def test_a_case_past_its_deadline_is_cancelled_and_fails_as_timed_out() -> None:
    worker = Worker(slots=2)
    # The goals never finish (they never even get past planning).
    store, goals = Store(), GoalService(None)
    _queued(store)
    cfg = RunConfig(concurrency=5, poll_seconds=0.01, lease_seconds=30, case_timeout=0.05)
    try:
        await _drive(worker, store, goals, cfg)
    finally:
        worker.shutdown()
    result = store.results["r1"]
    assert result["status"] == "completed" and result["passed"] is False
    assert {c["goal_status"] for c in result["cases"]} == {"timeout"}
    assert all(c["status"] == "execution_failed" for c in result["cases"])
    assert sorted(goals.cancelled) == [f"g-{i}" for i in range(5)]


async def test_a_second_step_cannot_advance_a_leased_run() -> None:
    store, goals = Store(), GoalService(None)
    _queued(store)
    cfg = RunConfig(concurrency=1, poll_seconds=0.01, lease_seconds=30, case_timeout=10)
    row = await store.claim_run(_CTX.tenant_id, "r1", "other-step", 30)
    assert row is not None
    out = await run_step(store=store, tenant_ctx=_CTX, result_id="r1", goal_service=goals,
                         provider=None, cfg=cfg, owner="me")
    assert out["status"] == "busy"
    assert goals.goals == {}


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_a_goal_that_did_not_complete_fails_its_case(status: str) -> None:
    store, goals = Store(), GoalService(None)
    _queued(store)
    cfg = RunConfig(concurrency=5, poll_seconds=0.01, lease_seconds=30, case_timeout=10)
    await run_step(store=store, tenant_ctx=_CTX, result_id="r1", goal_service=goals,
                   provider=None, cfg=cfg)
    for rec in goals.goals.values():
        rec["status"] = status
    out = await run_step(store=store, tenant_ctx=_CTX, result_id="r1", goal_service=goals,
                         provider=None, cfg=cfg)
    assert out["status"] == "completed"
    result = store.results["r1"]
    assert {c["status"] for c in result["cases"]} == {"execution_failed"}
    assert result["executed_cases"] == 0 and result["passed"] is False


def test_celery_step_reenqueues_itself_and_never_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    store, goals = Store(), GoalService(None)
    _queued(store)
    monkeypatch.setattr(tasks, "_ai_ops_worker_deps", lambda: (store, goals, None, ""))
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_ai_ops_dataset, "apply_async", lambda **kw: calls.append(kw)
    )
    kwargs = {"tenant_id": _CTX.tenant_id, "plan": "enterprise", "result_id": "r1"}
    t0 = time.monotonic()
    out = tasks.run_ai_ops_dataset.apply(kwargs=kwargs).get()
    assert time.monotonic() - t0 < 5
    assert out["status"] == "running"
    assert calls and calls[0]["kwargs"] == kwargs and calls[0]["countdown"] > 0
    assert calls[0]["queue"] == "maintenance"
    assert len(store.results["r1"]["inflight"]) == 4  # submitted, not awaited

    # Every goal finished: the next step completes the run and stops the chain.
    for rec in goals.goals.values():
        rec["status"] = "complete"
        rec["events"] = [{"type": "step_complete", "output": "x"}]
    calls.clear()
    for _ in range(3):
        out = tasks.run_ai_ops_dataset.apply(kwargs=kwargs).get()
        if out["status"] != "running":
            break
        for rec in goals.goals.values():
            rec["status"] = "complete"
    assert out["status"] == "completed"
    assert store.results["r1"]["total_cases"] == 5
