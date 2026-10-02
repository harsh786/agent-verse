"""Legal transitions for the handoff lifecycle."""

from app.coordination.handoffs.models import HandoffState
from app.coordination.state_machines import InvalidTransitionError

HANDOFF_TRANSITIONS: dict[HandoffState, frozenset[HandoffState]] = {
    HandoffState.REQUESTED: frozenset(
        {HandoffState.ACCEPTED, HandoffState.REJECTED, HandoffState.EXPIRED, HandoffState.CANCELLED}
    ),
    HandoffState.ACCEPTED: frozenset(
        {HandoffState.EXECUTING, HandoffState.CANCELLED, HandoffState.EXPIRED}
    ),
    # A target that crashed mid-work never reports: past the deadline the sweeper
    # expires the handoff so the parent session resumes (ORG-38).
    HandoffState.EXECUTING: frozenset(
        {
            HandoffState.COMPLETED,
            HandoffState.FAILED,
            HandoffState.CANCELLED,
            HandoffState.EXPIRED,
        }
    ),
    HandoffState.REJECTED: frozenset(),
    HandoffState.EXPIRED: frozenset(),
    HandoffState.COMPLETED: frozenset(),
    HandoffState.FAILED: frozenset(),
    HandoffState.CANCELLED: frozenset(),
}


def transition_handoff(current: HandoffState, target: HandoffState) -> HandoffState:
    if target not in HANDOFF_TRANSITIONS[current]:
        raise InvalidTransitionError(f"invalid handoff transition: {current} -> {target}")
    return target


__all__ = ["HANDOFF_TRANSITIONS", "transition_handoff"]
