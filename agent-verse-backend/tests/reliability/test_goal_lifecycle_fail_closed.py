"""a08-F193-03: pause/cancel flag reads fail closed on a Redis error.

is_paused/is_cancelled (and the _sync variants) used to return False on any
read error, so one Redis blip hid a cancel from a runner and, inside the pause
wait loops, ended an operator's pause (the goal resumed and emitted
goal_execution_resumed without any resume_goal call).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.reliability.goal_lifecycle import (
    GoalCancelledError,
    is_cancelled,
    is_cancelled_sync,
    is_paused,
    is_paused_sync,
)


def _broken_async() -> AsyncMock:
    r = AsyncMock()
    r.get = AsyncMock(side_effect=ConnectionError("redis down"))
    return r


def _broken_sync() -> MagicMock:
    r = MagicMock()
    r.get = MagicMock(side_effect=ConnectionError("redis down"))
    return r


@pytest.mark.asyncio
async def test_is_cancelled_treats_a_read_error_as_cancelled() -> None:
    assert await is_cancelled("g", _broken_async()) is True


@pytest.mark.asyncio
async def test_is_paused_treats_a_read_error_as_still_paused() -> None:
    assert await is_paused("g", _broken_async()) is True


def test_is_cancelled_sync_treats_a_read_error_as_cancelled() -> None:
    assert is_cancelled_sync("g", _broken_sync()) is True


def test_is_paused_sync_treats_a_read_error_as_still_paused() -> None:
    assert is_paused_sync("g", _broken_sync()) is True


@pytest.mark.asyncio
async def test_clean_reads_are_unchanged() -> None:
    ok = AsyncMock()
    ok.get = AsyncMock(return_value=None)
    assert await is_cancelled("g", ok) is False
    assert await is_paused("g", ok) is False
    ok.get = AsyncMock(return_value="1")
    assert await is_cancelled("g", ok) is True
    assert await is_paused("g", ok) is True


# The step-boundary pause gate is the worker's own (_make_worker_pause_gate);
# the duplicate check_pause_cancel had no callers and was removed (a08-F193-02).


@pytest.mark.asyncio
async def test_worker_gate_stops_instead_of_running_on_when_redis_is_down() -> None:
    from app.scaling.tasks import _make_worker_pause_gate

    gate = _make_worker_pause_gate("g", _broken_sync(), None)
    with pytest.raises(GoalCancelledError):
        await gate()


@pytest.mark.asyncio
async def test_a_read_blip_during_a_pause_does_not_resume_the_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused → one failed pause read → the goal must still be paused, not resumed."""
    import app.scaling.tasks as tasks_mod

    reads: list[str] = []

    def get(key: str) -> str | None:
        reads.append(key)
        if key.startswith("goal_cancelled:"):
            return None
        n = sum(1 for k in reads if k.startswith("goal_paused:"))
        if n == 1:
            return "1"  # paused on entry
        if n == 2:
            raise ConnectionError("blip")  # one failed read inside the wait loop
        if n == 3:
            return "1"  # still paused once Redis is back
        return None  # operator resumes

    r = MagicMock()
    r.get = MagicMock(side_effect=get)
    sleeps: list[float] = []

    async def fast_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(tasks_mod.asyncio, "sleep", fast_sleep)
    await tasks_mod._make_worker_pause_gate("g", r, None)()
    # Fail-open: the blip read would end the pause before any poll (0 sleeps).
    # Fail-closed: it keeps waiting until the flag is really gone (2 polls).
    assert len(sleeps) == 2
