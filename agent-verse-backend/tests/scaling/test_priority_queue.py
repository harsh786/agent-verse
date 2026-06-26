"""Tests for Phase 9 — Priority Queue and parallel step execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.scaling import tasks
from app.scaling.parallel_executor import ParallelExecutor
from app.scaling.priority_queue import Priority, PriorityQueue, Task
from app.scaling.tasks import _scheduled_goal_kwargs, run_goal

# ── PriorityQueue ──────────────────────────────────────────────────────────────

def test_priority_queue_dequeues_highest_priority_first() -> None:
    pq = PriorityQueue()
    pq.enqueue(Task(task_id="low", goal="low priority", priority=Priority.P4))
    pq.enqueue(Task(task_id="high", goal="critical", priority=Priority.P0))
    pq.enqueue(Task(task_id="medium", goal="medium", priority=Priority.P2))

    first = pq.dequeue()
    assert first is not None
    assert first.priority == Priority.P0


def test_priority_queue_fifo_within_same_priority() -> None:
    pq = PriorityQueue()
    pq.enqueue(Task(task_id="t1", goal="first", priority=Priority.P1))
    pq.enqueue(Task(task_id="t2", goal="second", priority=Priority.P1))
    pq.enqueue(Task(task_id="t3", goal="third", priority=Priority.P1))

    ids = [pq.dequeue().task_id for _ in range(3)]  # type: ignore[union-attr]
    assert ids == ["t1", "t2", "t3"]


def test_priority_queue_empty_returns_none() -> None:
    pq = PriorityQueue()
    assert pq.dequeue() is None


def test_priority_queue_all_5_priority_levels() -> None:
    for p in Priority:
        assert p.value in (0, 1, 2, 3, 4)


def test_priority_queue_len() -> None:
    pq = PriorityQueue()
    pq.enqueue(Task(task_id="a", goal="x", priority=Priority.P2))
    pq.enqueue(Task(task_id="b", goal="y", priority=Priority.P2))
    assert len(pq) == 2


# ── ParallelExecutor ──────────────────────────────────────────────────────────

async def test_parallel_executor_runs_independent_steps() -> None:
    results: list[str] = []

    async def step_a() -> str:
        results.append("a")
        return "done_a"

    async def step_b() -> str:
        results.append("b")
        return "done_b"

    executor = ParallelExecutor(max_concurrency=4)
    outputs = await executor.run_parallel([step_a, step_b])
    assert sorted(outputs) == ["done_a", "done_b"]


async def test_parallel_executor_respects_concurrency_limit() -> None:
    active_count = 0
    max_active = 0

    async def step() -> str:
        nonlocal active_count, max_active
        active_count += 1
        max_active = max(max_active, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return "done"

    executor = ParallelExecutor(max_concurrency=2)
    steps: list[Callable[[], Awaitable[str]]] = [step for _ in range(6)]
    await executor.run_parallel(steps)
    assert max_active <= 2


async def test_parallel_executor_handles_empty() -> None:
    executor = ParallelExecutor(max_concurrency=4)
    results = await executor.run_parallel([])
    assert results == []


def test_scheduled_goal_kwargs_includes_agent_id_and_goal_template() -> None:
    schedule_key = "schedule:tid-sched:sched-1"
    payload = _scheduled_goal_kwargs(
        schedule_key,
        {
            "tenant_id": "tid-sched",
            "goal_id": "legacy-fallback",
            "goal_template": "Run daily report",
            "agent_id": "agent-abc",
        },
    )

    assert payload is not None
    assert payload == {
        "goal_id": payload["goal_id"],
        "goal_text": "Run daily report",
        "goal_template": "Run daily report",
        "tenant_id": "tid-sched",
        "agent_id": "agent-abc",
    }
    assert payload["goal_id"].startswith("sched_")
    assert len(payload["goal_id"]) <= 32
    assert ":" not in payload["goal_id"]


def test_scheduled_goal_kwargs_bind_to_run_goal_signature() -> None:
    payload = _scheduled_goal_kwargs(
        "schedule:tid-sched:sched-1",
        {
            "tenant_id": "tid-sched",
            "goal_template": "Run daily report",
            "agent_id": "agent-abc",
        },
    )

    assert payload is not None
    inspect.signature(run_goal.run).bind(**payload)


def test_scheduled_goal_id_is_deterministic_bounded_and_safe() -> None:
    schedule_key = "schedule:tenant-with-long-id:uuid-1234567890abcdef1234567890abcdef"

    assert hasattr(tasks, "_scheduled_goal_id")
    assert "fire_instance_id" in inspect.signature(tasks._scheduled_goal_id).parameters
    first_goal_id = tasks._scheduled_goal_id(
        schedule_key,
        fire_instance_id="2026-06-25T10:00:00",
    )
    second_goal_id = tasks._scheduled_goal_id(
        schedule_key,
        fire_instance_id="2026-06-25T10:01:00",
    )
    duplicate_goal_id = tasks._scheduled_goal_id(
        schedule_key,
        fire_instance_id="2026-06-25T10:00:00",
    )

    assert duplicate_goal_id == first_goal_id
    assert first_goal_id != second_goal_id
    for goal_id in (first_goal_id, second_goal_id):
        assert goal_id.startswith("sched_")
        assert len(goal_id) <= 32
        assert ":" not in goal_id


def test_direct_goal_kwargs_bind_to_run_goal_signature() -> None:
    payload = {
        "goal_id": "goal-123",
        "tenant_id": "tid-direct",
        "goal_text": "Run deployment verification",
        "priority": "high",
        "dry_run": False,
        "agent_id": "agent-abc",
        "workflow_mode": "multi_agent",
    }

    inspect.signature(run_goal.run).bind(**payload)


def test_run_goal_updates_submitted_goal_status_and_events(monkeypatch: Any) -> None:
    from app.scaling import tasks

    status_updates: list[str] = []
    appended_events: list[dict[str, Any]] = []
    duration_metrics: list[dict[str, Any]] = []

    class FakeState:
        status = SimpleNamespace(value="complete")
        iterations = 2

    class FakeAgentLoop:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> FakeState:
            callback = kwargs.get("event_callback")
            if callback is not None:
                await callback({"type": "plan_ready", "steps": ["step 1"]})
                await callback({"type": "step_complete", "step": "step 1"})
            return FakeState()

    class FakeGoalService:
        def __init__(self, *, db_session_factory: Any, event_store: Any) -> None:
            self._event_store = event_store

        async def _db_ensure_goal_row(self, **kwargs: Any) -> None:
            assert kwargs["goal_id"] == "goal-123"
            assert kwargs["tenant_id"] == "tenant-1"
            assert kwargs["goal_text"] == "Run deployment verification"

        async def _db_update_goal_status(
            self,
            goal_id: str,
            tenant_id: str,
            status: str,
            error_message: str = "",
            iterations: int = 0,
        ) -> None:
            assert goal_id == "goal-123"
            assert tenant_id == "tenant-1"
            status_updates.append(status)

    class FakeEventStore:
        def __init__(self, db_session_factory: Any) -> None:
            pass

        async def append_event(
            self, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any
        ) -> None:
            assert goal_id == "goal-123"
            assert tenant_ctx.tenant_id == "tenant-1"
            appended_events.append(dict(event))

    monkeypatch.setattr("app.agent.loop.AgentLoop", FakeAgentLoop)
    monkeypatch.setattr("app.services.goal_service.GoalService", FakeGoalService)
    monkeypatch.setattr("app.services.event_store.EventStore", FakeEventStore)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monotonic_values = iter([10.0, 12.5])
    monkeypatch.setattr(tasks, "_monotonic", lambda: next(monotonic_values), raising=False)
    monkeypatch.setattr(
        "app.observability.metrics.record_goal_duration",
        lambda status, duration_seconds, priority: duration_metrics.append(
            {
                "status": status,
                "duration_seconds": duration_seconds,
                "priority": priority,
            }
        ),
    )

    result = run_goal.run(
        goal_id="goal-123",
        tenant_id="tenant-1",
        goal_text="Run deployment verification",
        priority="high",
    )

    assert status_updates == ["executing", "complete"]
    assert [event["type"] for event in appended_events] == [
        "worker_started",
        "plan_ready",
        "step_complete",
        "worker_complete",
    ]
    assert result["result_scope"] == "submitted_goal"
    assert result["submitted_goal_status"] == "complete"
    assert result["status_bridge"] == "updated"
    assert duration_metrics == [
        {"status": "complete", "duration_seconds": 2.5, "priority": "high"}
    ]


def test_run_goal_records_duration_metric_on_worker_failure(monkeypatch: Any) -> None:
    duration_metrics: list[dict[str, Any]] = []

    class FakeAgentLoop:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            raise RuntimeError("worker failed")

    class FakeGoalService:
        def __init__(self, *, db_session_factory: Any, event_store: Any) -> None:
            pass

        async def _db_ensure_goal_row(self, **kwargs: Any) -> None:
            pass

        async def _db_update_goal_status(
            self,
            goal_id: str,
            tenant_id: str,
            status: str,
            error_message: str = "",
            iterations: int = 0,
        ) -> None:
            pass

    class FakeEventStore:
        def __init__(self, db_session_factory: Any) -> None:
            pass

        async def append_event(
            self, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any
        ) -> None:
            pass

    monkeypatch.setattr("app.agent.loop.AgentLoop", FakeAgentLoop)
    monkeypatch.setattr("app.services.goal_service.GoalService", FakeGoalService)
    monkeypatch.setattr("app.services.event_store.EventStore", FakeEventStore)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monotonic_values = iter([20.0, 23.25])
    monkeypatch.setattr(tasks, "_monotonic", lambda: next(monotonic_values), raising=False)
    monkeypatch.setattr(
        "app.observability.metrics.record_goal_duration",
        lambda status, duration_seconds, priority: duration_metrics.append(
            {
                "status": status,
                "duration_seconds": duration_seconds,
                "priority": priority,
            }
        ),
    )

    with pytest.raises(RuntimeError, match="worker failed"):
        run_goal.run(
            goal_id="goal-fail",
            tenant_id="tenant-1",
            goal_text="Run deployment verification",
            priority="critical",
        )

    assert duration_metrics == [
        {"status": "failed", "duration_seconds": 3.25, "priority": "critical"}
    ]


def test_run_goal_ensures_goal_row_before_status_and_events(monkeypatch: Any) -> None:
    order: list[str] = []

    class FakeGoalService:
        def __init__(self, *, db_session_factory: Any, event_store: Any) -> None:
            self._event_store = event_store

        async def _db_ensure_goal_row(
            self,
            *,
            goal_id: str,
            tenant_id: str,
            goal_text: str,
            status: str,
            priority: str,
            dry_run: bool,
            agent_id: str | None = None,
            workflow_mode: str = "single_agent",
            execution_context: dict[str, Any] | None = None,
        ) -> None:
            assert goal_id == "goal-missing"
            assert tenant_id == "tenant-1"
            assert goal_text == "Run scheduled report"
            assert status == "planning"
            assert priority == "normal"
            assert dry_run is True
            assert agent_id == "agent-abc"
            assert workflow_mode == "single_agent"
            assert execution_context == {}
            order.append("ensure")

        async def _db_update_goal_status(
            self,
            goal_id: str,
            tenant_id: str,
            status: str,
            error_message: str = "",
            iterations: int = 0,
        ) -> None:
            order.append(f"status:{status}")

    class FakeEventStore:
        def __init__(self, db_session_factory: Any) -> None:
            pass

        async def append_event(
            self, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any
        ) -> None:
            order.append(f"event:{event['type']}")

    monkeypatch.setattr("app.services.goal_service.GoalService", FakeGoalService)
    monkeypatch.setattr("app.services.event_store.EventStore", FakeEventStore)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())

    result = run_goal.run(
        goal_id="goal-missing",
        tenant_id="tenant-1",
        goal_text="Run scheduled report",
        dry_run=True,
        agent_id="agent-abc",
    )

    assert order == [
        "ensure",
        "status:executing",
        "event:worker_started",
        "status:complete",
        "event:worker_complete",
    ]
    assert result["status_bridge"] == "updated"
