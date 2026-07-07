"""RecoveryTrace — observability trace for recovery decisions."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.recovery.failure_classifier import FailureClass
from app.recovery.recovery_policy import RecoveryAction


@dataclass
class RecoveryTraceEntry:
    failure_class: FailureClass
    action_taken: RecoveryAction
    attempt: int
    reason: str = ""


class RecoveryTrace:
    def __init__(self, goal_id: str) -> None:
        self.goal_id = goal_id
        self.trace_id = uuid.uuid4().hex
        self.entries: list[RecoveryTraceEntry] = []

    def record(
        self,
        failure_class: FailureClass,
        action: RecoveryAction,
        attempt: int,
        reason: str = "",
    ) -> None:
        self.entries.append(
            RecoveryTraceEntry(
                failure_class=failure_class,
                action_taken=action,
                attempt=attempt,
                reason=reason,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "total_attempts": len(self.entries),
            "entries": [
                {
                    "failure_class": e.failure_class.value,
                    "action": e.action_taken.value,
                    "attempt": e.attempt,
                    "reason": e.reason,
                }
                for e in self.entries
            ],
        }
