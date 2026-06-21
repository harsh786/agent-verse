"""Tests for Phase 9 — Priority Queue and parallel step execution."""

from __future__ import annotations

import asyncio
import pytest

from app.scaling.priority_queue import PriorityQueue, Priority, Task
from app.scaling.parallel_executor import ParallelExecutor


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
    steps = [step for _ in range(6)]
    await executor.run_parallel(steps)
    assert max_active <= 2


async def test_parallel_executor_handles_empty() -> None:
    executor = ParallelExecutor(max_concurrency=4)
    results = await executor.run_parallel([])
    assert results == []
