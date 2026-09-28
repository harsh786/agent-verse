"""A2A task outcome: only a real terminal state may be reported, with real output.

Regression for ``execute_and_callback`` pre-setting ``final_status = "complete"``:
a cancelled goal (``goal_cancelled`` was ignored) or an event stream that ended
without a terminal event was reported to the calling agent as *completed*, and a
completed task's result was the literal ``"Goal <id> completed"``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.a2a import _await_goal_outcome


def _svc(events: list[dict[str, Any]], goal: dict[str, Any] | Exception) -> MagicMock:
    async def _subscribe(goal_id: str, tenant_ctx: Any) -> AsyncIterator[dict[str, Any]]:
        for e in events:
            yield e

    svc = MagicMock()
    svc.subscribe_events = MagicMock(side_effect=_subscribe)
    if isinstance(goal, Exception):
        svc.get_goal = AsyncMock(side_effect=goal)
    else:
        svc.get_goal = AsyncMock(return_value=goal)
    return svc


@pytest.mark.asyncio
async def test_cancelled_goal_is_reported_canceled_not_complete() -> None:
    svc = _svc([{"type": "step_start"}, {"type": "goal_cancelled"}], {"status": "cancelled"})
    status, result = await _await_goal_outcome(svc, "g1", object())
    assert status == "canceled"
    assert "cancel" in result


@pytest.mark.asyncio
async def test_completed_goal_returns_actual_output() -> None:
    goal = {"status": "complete", "result_artifact": {"summary": "The answer is 42."}}
    svc = _svc([{"type": "goal_complete"}], goal)
    status, result = await _await_goal_outcome(svc, "g1", object())
    assert status == "complete"
    assert result == "The answer is 42."
    assert "Goal g1 completed" not in result


@pytest.mark.asyncio
async def test_failed_goal_carries_reason() -> None:
    svc = _svc([{"type": "goal_failed", "reason": "budget exceeded"}], {"status": "failed"})
    assert await _await_goal_outcome(svc, "g1", object()) == ("failed", "budget exceeded")


@pytest.mark.asyncio
async def test_stream_ending_without_terminal_event_uses_persisted_status() -> None:
    svc = _svc([{"type": "step_start"}], {"status": "cancelled"})
    status, _ = await _await_goal_outcome(svc, "g1", object())
    assert status == "canceled"


@pytest.mark.asyncio
async def test_stream_ending_with_non_terminal_status_is_an_error_not_complete() -> None:
    svc = _svc([], {"status": "running"})
    status, result = await _await_goal_outcome(svc, "g1", object())
    assert status == "error"
    assert "terminal" in result


@pytest.mark.asyncio
async def test_stream_ending_and_status_lookup_failing_is_an_error() -> None:
    svc = _svc([], RuntimeError("db down"))
    status, _ = await _await_goal_outcome(svc, "g1", object())
    assert status == "error"


@pytest.mark.asyncio
async def test_timeout_is_reported() -> None:
    import asyncio

    async def _hang(goal_id: str, tenant_ctx: Any) -> AsyncIterator[dict[str, Any]]:
        await asyncio.sleep(10)
        yield {"type": "goal_complete"}

    svc = MagicMock()
    svc.subscribe_events = MagicMock(side_effect=_hang)
    status, _ = await _await_goal_outcome(svc, "g1", object(), timeout_s=0.05)
    assert status == "timeout"
