"""B1-3: a queued beat fire re-checks its schedule before creating a goal.

Live (2026-10-06): an interval schedule deleted at 00:13:31 still created goal
0137f7f7 at 00:14:11. The beat had enqueued ``run_scheduled_goal`` before the
delete, the task waited in the ``schedules`` queue behind running goals, and
it dispatched without looking at the schedule again. A paused schedule had the
same window. The task now reads the schedule row (tenant-scoped) first: a
deleted schedule creates nothing, a paused one is recorded as skipped.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scaling import tasks


async def _run(state: str | None, dispatch: AsyncMock) -> Any:
    with (
        patch.object(tasks, "_build_worker_goal_service", return_value=(MagicMock(), MagicMock())),
        patch.object(tasks, "_worker_async_redis", return_value=None),
        patch.object(tasks, "_schedule_fire_state", new=AsyncMock(return_value=state)),
        patch.object(tasks, "_dispatch_scheduled_via_dispatcher", new=dispatch),
    ):
        return await tasks._run_scheduled_goal_governed(
            "schedule:t1:s1", "t1", "do x", "", "2026-10-06T00:14:00"
        )


@pytest.mark.asyncio
async def test_a_deleted_schedule_creates_no_goal() -> None:
    dispatch = AsyncMock()
    event = await _run("deleted", dispatch)
    dispatch.assert_not_awaited()
    assert event.goal_created is False
    assert event.skip_reason == "schedule_deleted"


@pytest.mark.asyncio
async def test_a_paused_schedule_creates_no_goal() -> None:
    dispatch = AsyncMock()
    event = await _run("paused", dispatch)
    dispatch.assert_not_awaited()
    assert event.goal_created is False
    assert event.skip_reason == "schedule_paused"


@pytest.mark.asyncio
async def test_an_active_schedule_is_dispatched() -> None:
    fired = SimpleNamespace(goal_created=True, goal_id="g1", skip_reason=None)
    dispatch = AsyncMock(return_value=fired)
    assert await _run(None, dispatch) is fired
    dispatch.assert_awaited_once()


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Session:
    def __init__(self, row: Any, log: list[tuple[str, dict[str, Any]]]) -> None:
        self._row = row
        self._log = log

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self._log.append((str(stmt), dict(params or {})))
        return _Result(self._row)

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> Any:
        return self


def _factory(row: Any, log: list[tuple[str, dict[str, Any]]]) -> Any:
    return lambda: _Session(row, log)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [(None, "deleted"), (SimpleNamespace(paused=True), "paused"),
     (SimpleNamespace(paused=False), None)],
)
async def test_schedule_fire_state_reads_the_tenants_row(row: Any, expected: str | None) -> None:
    log: list[tuple[str, dict[str, Any]]] = []
    state = await tasks._schedule_fire_state(_factory(row, log), "t1", "schedule:t1:s1")
    assert state == expected
    sql, params = next((s, p) for s, p in log if "FROM schedules" in s)
    # Explicit tenant predicate on top of RLS, bare schedule id.
    assert "tenant_id = :tid" in sql
    assert params == {"sid": "s1", "tid": "t1"}


@pytest.mark.asyncio
async def test_schedule_fire_state_without_a_database_does_not_block() -> None:
    assert await tasks._schedule_fire_state(None, "t1", "s1") is None
