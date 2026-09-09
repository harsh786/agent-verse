"""Tests for Phase 5: stuck-loop detection in AgentGraph._check_stuck_loop."""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_state_with_statuses(statuses: list[str]) -> object:
    """Build a minimal AgentState-like object with steps of given statuses."""
    from app.agent.state import StepStatus

    state = MagicMock()
    steps = []
    for s in statuses:
        sr = MagicMock()
        sr.status = StepStatus.FAILED if s == "failed" else StepStatus.COMPLETE
        steps.append(sr)
    state.steps = steps
    return state


class TestStuckLoopDetection:
    def _get_graph(self) -> object:
        from app.agent.graph import AgentGraph
        # Minimal AgentGraph — just need _check_stuck_loop method
        g = object.__new__(AgentGraph)
        return g

    def test_returns_false_when_no_steps(self) -> None:
        graph = self._get_graph()
        state = MagicMock()
        state.steps = []
        assert graph._check_stuck_loop(state, window=3) is False  # type: ignore[attr-defined]

    def test_returns_false_when_fewer_than_window_steps(self) -> None:
        graph = self._get_graph()
        state = _make_state_with_statuses(["failed", "failed"])
        assert graph._check_stuck_loop(state, window=3) is False  # type: ignore[attr-defined]

    def test_returns_true_when_last_3_are_failed(self) -> None:
        graph = self._get_graph()
        state = _make_state_with_statuses(["complete", "failed", "failed", "failed"])
        assert graph._check_stuck_loop(state, window=3) is True  # type: ignore[attr-defined]

    def test_returns_false_when_last_3_mixed(self) -> None:
        graph = self._get_graph()
        state = _make_state_with_statuses(["failed", "complete", "failed"])
        assert graph._check_stuck_loop(state, window=3) is False  # type: ignore[attr-defined]

    def test_returns_false_when_all_complete(self) -> None:
        graph = self._get_graph()
        state = _make_state_with_statuses(["complete", "complete", "complete"])
        assert graph._check_stuck_loop(state, window=3) is False  # type: ignore[attr-defined]
