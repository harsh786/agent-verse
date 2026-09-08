"""Behavioral tests for the Magentic stall detector's counter isolation."""

from __future__ import annotations

import pytest

from app.coordination.magentic.models import ProgressAssessment
from app.coordination.magentic.stall_detector import StallDetector


def _observe(
    detector: StallDetector,
    version: int,
    *,
    progressed: bool,
    action: str,
    blockers: tuple[str, ...],
):
    return detector.observe(
        revision_version=version,
        assessment=ProgressAssessment(progressed=progressed),
        action_signature=action,
        blockers=blockers,
    )


def test_stall_detector_rejects_non_positive_thresholds() -> None:
    with pytest.raises(ValueError, match="thresholds must be positive"):
        StallDetector(max_no_progress=0, max_repeated_actions=2)
    with pytest.raises(ValueError, match="thresholds must be positive"):
        StallDetector(max_no_progress=2, max_repeated_actions=0)


def test_changing_action_resets_repeated_but_no_progress_keeps_climbing() -> None:
    detector = StallDetector(max_no_progress=5, max_repeated_actions=3)
    first = _observe(detector, 1, progressed=False, action="alpha", blockers=("b",))
    second = _observe(detector, 2, progressed=False, action="alpha", blockers=("b",))
    # A different action breaks the repeated-action streak...
    third = _observe(detector, 3, progressed=False, action="beta", blockers=("b",))
    assert first.repeated_action_count == 1
    assert second.repeated_action_count == 2
    assert third.repeated_action_count == 1
    # ...but no-progress is cumulative regardless of which action ran.
    assert third.consecutive_no_progress == 3
    assert not third.stalled


def test_repeated_action_alone_trips_the_stall_signal() -> None:
    detector = StallDetector(max_no_progress=10, max_repeated_actions=2)
    _observe(detector, 1, progressed=False, action="same", blockers=("x",))
    result = _observe(detector, 2, progressed=False, action="same", blockers=("y",))
    assert result.stalled and "repeated_action" in result.reasons
    # no_progress threshold (10) is nowhere near tripped; only repetition fired.
    assert "no_progress" not in result.reasons


def test_changing_blockers_resets_unchanged_blocker_counter() -> None:
    detector = StallDetector(max_no_progress=2, max_repeated_actions=10)
    _observe(detector, 1, progressed=False, action="a", blockers=("stuck",))
    moved = _observe(detector, 2, progressed=False, action="b", blockers=("different",))
    # Blockers changed, so the unchanged-blocker streak restarts at 1.
    assert moved.unchanged_blocker_count == 1
    assert "unchanged_blockers" not in moved.reasons
    # But two no-progress rounds still trip the no_progress reason.
    assert moved.consecutive_no_progress == 2 and "no_progress" in moved.reasons
