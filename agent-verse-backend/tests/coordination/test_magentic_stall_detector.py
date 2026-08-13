from __future__ import annotations

from app.coordination.magentic.models import ProgressAssessment
from app.coordination.magentic.stall_detector import StallDetector


def test_stall_thresholds_and_duplicate_revision_are_deterministic() -> None:
    detector = StallDetector(max_no_progress=2, max_repeated_actions=2)
    first = detector.observe(
        revision_version=1,
        assessment=ProgressAssessment(progressed=False),
        action_signature="tool:error",
        blockers=("blocked",),
    )
    duplicate = detector.observe(
        revision_version=1,
        assessment=ProgressAssessment(progressed=False),
        action_signature="tool:error",
        blockers=("blocked",),
    )
    second = detector.observe(
        revision_version=2,
        assessment=ProgressAssessment(progressed=False),
        action_signature="tool:error",
        blockers=("blocked",),
    )
    assert duplicate == first and not first.stalled
    assert second.stalled
    assert {"no_progress", "repeated_action", "unchanged_blockers"} <= set(second.reasons)


def test_material_progress_resets_stall_counters() -> None:
    detector = StallDetector(max_no_progress=2, max_repeated_actions=2)
    detector.observe(
        revision_version=1,
        assessment=ProgressAssessment(progressed=False),
        action_signature="same",
        blockers=("blocked",),
    )
    result = detector.observe(
        revision_version=2,
        assessment=ProgressAssessment(progressed=True, accepted_kinds=("evidence",)),
        action_signature="new",
        blockers=(),
    )
    assert not result.stalled and result.consecutive_no_progress == 0
