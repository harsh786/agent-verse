"""MEM-25: AI-Ops dataset runs are durable worker tasks that resume per case.

A run was an asyncio task on the API replica that received the request; a
deploy or crash lost it. It now runs as non-blocking Celery steps
(``run_ai_ops_dataset``, acks_late + reject_on_worker_lost, P7-1); every
finished case and every submitted goal id is persisted, and a redelivered step
executes only the cases that are left.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ai_ops as ai_ops_api
from app.evals.ai_ops_jobs import MemoryRunLease, RunConfig, execute_dataset_run
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-aiops-durable", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_TASKS = [{"input": f"task {i}", "expected_output": f"answer {i}"} for i in range(4)]


class WorkerLost(BaseException):
    """Stands in for the worker process dying mid-run."""


class _Store:
    """Durable-store double shared by the 'workers' of one test."""

    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}
        self.baselines: dict[str, float] = {}
        self.alerts: list[dict[str, Any]] = []

    async def get_eval_result(self, tenant_id: str, result_id: str) -> dict[str, Any] | None:
        row = self.results.get(result_id)
        return copy.deepcopy(row) if row is not None else None

    async def update_eval_result(
        self, *, tenant_id: str, result_id: str, payload: dict[str, Any]
    ) -> None:
        self.results[result_id] = copy.deepcopy(payload)

    async def add_eval_result(
        self, *, tenant_id: str, result_id: str, dataset_id: str, payload: dict[str, Any]
    ) -> None:
        self.results[result_id] = copy.deepcopy(payload)

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
        return {"dataset_id": dataset_id, "golden_tasks": _TASKS}

    async def get_judge(self, tenant_id: str, judge_id: str) -> dict[str, Any] | None:
        return None

    async def get_baseline(self, tenant_id: str, metric_name: str) -> float | None:
        return self.baselines.get(metric_name)

    async def add_alert(self, *, tenant_id: str, alert: dict[str, Any]) -> None:
        self.alerts.append(alert)

    async def set_baseline_if_absent(
        self, *, tenant_id: str, metric_name: str, value: float
    ) -> None:
        self.baselines.setdefault(metric_name, value)


class _GoalService:
    """Every goal completes with the expected answer; optionally dies at case N."""

    def __init__(self, *, die_on: str | None = None) -> None:
        self.die_on = die_on
        self.submitted: list[str] = []
        self._task_queue = None

    async def submit_goal(self, *, goal: str, **_: Any) -> dict[str, Any]:
        if goal == self.die_on:
            raise WorkerLost
        self.submitted.append(goal)
        return {"goal_id": f"g-{goal}"}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {"goal_id": goal_id, "status": "complete"}

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        n = goal_id.rsplit(" ", 1)[-1]
        return [{"type": "goal_complete", "answer": f"answer {n}"}]


# A lease that a dead step leaves behind expires almost at once here.
_CFG = RunConfig(concurrency=1, poll_seconds=0, lease_seconds=0.01, case_timeout=60)


def _queued(store: _Store, result_id: str = "r1") -> None:
    store.results[result_id] = {
        "result_id": result_id, "dataset_id": "d1", "tenant_id": _CTX.tenant_id,
        "agent_id": None, "status": "queued", "passed": False, "total_cases": len(_TASKS),
        "judge": None, "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


async def test_a_run_interrupted_mid_way_resumes_only_the_remaining_cases() -> None:
    store = _Store()
    _queued(store)

    first = _GoalService(die_on="task 2")
    with pytest.raises(WorkerLost):
        await execute_dataset_run(
            store=store, tenant_ctx=_CTX, result_id="r1", goal_service=first,
            provider=None, cfg=_CFG,
        )
    saved = store.results["r1"]
    assert saved["status"] == "running"
    done_before = {c["index"] for c in saved["cases"]}
    assert {0} <= done_before and 2 not in done_before

    await asyncio.sleep(0.02)  # the dead step's lease expires
    second = _GoalService()
    final = await execute_dataset_run(
        store=store, tenant_ctx=_CTX, result_id="r1", goal_service=second,
        provider=None, cfg=_CFG,
    )
    assert final is not None and final["status"] == "completed"
    # Only the cases the first attempt did not submit are executed again; the
    # goals it had submitted are polled, never resubmitted.
    assert sorted(first.submitted + second.submitted) == [f"task {i}" for i in range(4)]
    assert final["total_cases"] == 4 and final["scored_cases"] == 4
    assert store.results["r1"]["passed"] is True


async def test_a_redelivered_finished_run_is_not_executed_again() -> None:
    store = _Store()
    _queued(store)
    await execute_dataset_run(
        store=store, tenant_ctx=_CTX, result_id="r1", goal_service=_GoalService(),
        provider=None, cfg=_CFG,
    )
    again = _GoalService()
    out = await execute_dataset_run(
        store=store, tenant_ctx=_CTX, result_id="r1", goal_service=again, provider=None,
        cfg=_CFG,
    )
    assert out is not None and out["status"] == "completed"
    assert again.submitted == []


def test_celery_task_survives_a_worker_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from app.core.config import get_settings
    from app.scaling import tasks

    monkeypatch.setattr(get_settings(), "ai_ops_lease_seconds", 0.01)
    store = _Store()
    _queued(store, "r-celery")
    first, second = _GoalService(die_on="task 1"), _GoalService()
    workers = iter([first])

    def _deps() -> tuple[Any, Any, Any, str]:
        return store, next(workers, second), None, ""

    monkeypatch.setattr(tasks, "_ai_ops_worker_deps", _deps)
    chained: list[dict[str, Any]] = []
    monkeypatch.setattr(tasks.run_ai_ops_dataset, "apply_async", lambda **kw: chained.append(kw))
    kwargs = {"tenant_id": _CTX.tenant_id, "plan": "professional", "result_id": "r-celery"}
    with pytest.raises(WorkerLost):
        tasks.run_ai_ops_dataset.apply(kwargs=kwargs)
    saved = store.results["r-celery"]
    assert saved["status"] == "running"
    assert "0" in saved["inflight"]  # the goal submitted before the crash is recorded

    time.sleep(0.02)  # the dead step's lease expires; the message is redelivered
    out: dict[str, Any] = {}
    for _ in range(10):
        out = tasks.run_ai_ops_dataset.apply(kwargs=kwargs).get()
        if out["status"] != "running":
            break
    assert out == {"status": "completed", "result_id": "r-celery"}
    assert "task 0" not in second.submitted  # polled, never resubmitted
    assert sorted(first.submitted + second.submitted) == [f"task {i}" for i in range(4)]
    assert store.results["r-celery"]["scored_cases"] == 4
    assert chained and all(c["countdown"] > 0 for c in chained)


# ── API: queue to the worker when a durable store + Celery are wired ─────────


def _client(store: Any, goal_service: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k-aiops" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(ai_ops_api.router)
    app.state.ai_ops_store = store
    app.state.goal_service = goal_service
    return TestClient(app, raise_server_exceptions=False)


def test_run_is_queued_to_a_worker_in_celery_mode() -> None:
    store = _Store()
    gs = _GoalService()
    gs._task_queue = object()  # Celery-backed goal queue
    with patch("app.scaling.tasks.run_ai_ops_dataset.apply_async") as enqueue:
        r = _client(store, gs).post(
            "/ai-ops/datasets/d1/run", json={}, headers={"X-API-Key": "k-aiops"}
        )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    kwargs = enqueue.call_args.kwargs["kwargs"]
    assert kwargs["tenant_id"] == _CTX.tenant_id and kwargs["plan"] == "professional"
    assert store.results[kwargs["result_id"]]["status"] == "queued"
    assert gs.submitted == []  # nothing ran on the API replica


def test_a_run_that_cannot_be_queued_is_503_and_marked_failed() -> None:
    store = _Store()
    gs = _GoalService()
    gs._task_queue = object()
    with patch(
        "app.scaling.tasks.run_ai_ops_dataset.apply_async", side_effect=ConnectionError("down")
    ):
        r = _client(store, gs).post(
            "/ai-ops/datasets/d1/run", json={}, headers={"X-API-Key": "k-aiops"}
        )
    assert r.status_code == 503
    (only,) = store.results.values()
    assert only["status"] == "failed"


def test_staleness_follows_progress_not_age() -> None:
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()
    fresh = datetime.datetime.now(datetime.UTC).isoformat()
    running = {"status": "running", "created_at": old, "heartbeat_at": fresh}
    assert ai_ops_api._with_staleness(running)["status"] == "running"
    queued = {"status": "queued", "created_at": old}
    assert ai_ops_api._with_staleness(queued)["status"] == "abandoned"
