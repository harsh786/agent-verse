"""QoSTrace — observability trace for scheduling decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.qos.priority_policy import QueuePriority


@dataclass
class QoSTraceEntry:
    goal_id: str
    queue_name: str
    priority: QueuePriority
    latency_ms: float = 0.0


class QoSTrace:
    def __init__(self) -> None:
        self.trace_id = uuid.uuid4().hex
        self.entries: list[QoSTraceEntry] = []

    def record(
        self,
        goal_id: str,
        queue_name: str,
        priority: QueuePriority,
        latency_ms: float = 0.0,
    ) -> None:
        self.entries.append(
            QoSTraceEntry(
                goal_id=goal_id,
                queue_name=queue_name,
                priority=priority,
                latency_ms=latency_ms,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "total_scheduled": len(self.entries),
            "entries": [
                {
                    "goal_id": e.goal_id,
                    "queue": e.queue_name,
                    "priority": e.priority.value,
                }
                for e in self.entries
            ],
        }
