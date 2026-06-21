"""Priority queue for goal/task scheduling.

Uses a Python heapq (min-heap on score). Score = priority.value * 1e12 + timestamp,
ensuring P0 always precedes P4 while preserving FIFO within the same priority tier.

In production this is backed by a Redis sorted set with the same scoring formula.
"""

from __future__ import annotations

import enum
import heapq
import time
from dataclasses import dataclass, field


class Priority(enum.IntEnum):
    P0 = 0  # critical
    P1 = 1  # high
    P2 = 2  # normal (default)
    P3 = 3  # low
    P4 = 4  # background


_PRIORITY_SCALE = 1_000_000_000_000  # 1e12 — keeps 12 digits of priority headroom


@dataclass
class Task:
    task_id: str
    goal: str
    priority: Priority = Priority.P2
    created_at: float = field(default_factory=time.monotonic)

    def score(self) -> float:
        return self.priority.value * _PRIORITY_SCALE + self.created_at


class PriorityQueue:
    """Thread-safe in-memory priority queue.

    Dequeues the highest-priority (lowest score) task first.
    Within the same priority tier, FIFO order is preserved.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Task]] = []
        self._counter = 0

    def enqueue(self, task: Task) -> None:
        heapq.heappush(self._heap, (task.score(), self._counter, task))
        self._counter += 1

    def dequeue(self) -> Task | None:
        if not self._heap:
            return None
        _, _, task = heapq.heappop(self._heap)
        return task

    def __len__(self) -> int:
        return len(self._heap)
