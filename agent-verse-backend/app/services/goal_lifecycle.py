"""Goal lifecycle state management utilities.

Documents the intended decomposition of GoalService.
Handles state transitions: submit → planning → executing → verifying → complete/failed
"""

from __future__ import annotations

from enum import Enum


class GoalTransition(str, Enum):
    """Valid goal state transitions."""

    SUBMIT = "submit"
    START_PLANNING = "start_planning"
    START_EXECUTING = "start_executing"
    START_VERIFYING = "start_verifying"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    AWAIT_HUMAN = "await_human"


# Valid state machine transitions
_VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending": ["planning"],
    "planning": ["executing", "failed", "cancelled"],
    "executing": ["verifying", "failed", "cancelled", "waiting_human"],
    "verifying": ["complete", "failed", "executing"],  # re-execute on verify fail
    "waiting_human": ["executing", "cancelled"],
    "complete": [],  # terminal
    "failed": [],  # terminal
    "cancelled": [],  # terminal
    "paused": ["executing"],
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check if a goal state transition is valid."""
    return to_status in _VALID_TRANSITIONS.get(from_status, [])
