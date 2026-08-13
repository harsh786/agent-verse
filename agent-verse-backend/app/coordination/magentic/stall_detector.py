"""Deterministic no-progress, repeated-action, and blocker stall detection."""

from __future__ import annotations

import re

from app.coordination.magentic.models import ProgressAssessment, StallAssessment


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class StallDetector:
    def __init__(self, *, max_no_progress: int, max_repeated_actions: int) -> None:
        if max_no_progress <= 0 or max_repeated_actions <= 0:
            raise ValueError("stall thresholds must be positive")
        self._max_no_progress = max_no_progress
        self._max_repeated = max_repeated_actions
        self._seen: dict[int, StallAssessment] = {}
        self._no_progress = 0
        self._repeated = 0
        self._unchanged_blockers = 0
        self._last_action = ""
        self._last_blockers: tuple[str, ...] = ()

    def observe(
        self,
        *,
        revision_version: int,
        assessment: ProgressAssessment,
        action_signature: str,
        blockers: tuple[str, ...],
    ) -> StallAssessment:
        prior = self._seen.get(revision_version)
        if prior is not None:
            return prior
        action = _normalize(action_signature)
        normalized_blockers = tuple(sorted(_normalize(item) for item in blockers))
        if assessment.progressed:
            self._no_progress = self._repeated = self._unchanged_blockers = 0
        else:
            self._no_progress += 1
            self._repeated = self._repeated + 1 if action and action == self._last_action else 1
            self._unchanged_blockers = (
                self._unchanged_blockers + 1
                if normalized_blockers and normalized_blockers == self._last_blockers
                else 1
            )
        self._last_action = action
        self._last_blockers = normalized_blockers
        reasons: list[str] = []
        if self._no_progress >= self._max_no_progress:
            reasons.append("no_progress")
        if self._repeated >= self._max_repeated:
            reasons.append("repeated_action")
        if self._unchanged_blockers >= self._max_no_progress:
            reasons.append("unchanged_blockers")
        result = StallAssessment(
            stalled=bool(reasons),
            reasons=tuple(reasons),
            consecutive_no_progress=self._no_progress,
            repeated_action_count=self._repeated,
            unchanged_blocker_count=self._unchanged_blockers,
        )
        self._seen[revision_version] = result
        return result


__all__ = ["StallDetector"]
