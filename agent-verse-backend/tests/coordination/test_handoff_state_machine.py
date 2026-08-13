import pytest

from app.coordination.handoffs.models import HandoffState
from app.coordination.handoffs.state_machine import HANDOFF_TRANSITIONS, transition_handoff
from app.coordination.state_machines import InvalidTransitionError


def test_every_declared_handoff_transition_is_accepted() -> None:
    for current, targets in HANDOFF_TRANSITIONS.items():
        for target in targets:
            assert transition_handoff(current, target) is target


def test_terminal_and_skipped_transitions_are_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        transition_handoff(HandoffState.REQUESTED, HandoffState.COMPLETED)
    with pytest.raises(InvalidTransitionError):
        transition_handoff(HandoffState.COMPLETED, HandoffState.EXECUTING)
