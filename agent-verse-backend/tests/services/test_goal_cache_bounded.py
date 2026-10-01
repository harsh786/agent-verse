"""SVC-30: goal records loaded from Postgres must not pin replica memory forever.

_db_get_goal_record / sync_from_db cached GoalRecords without completed_at and
_evict_stale_goals skipped terminal records without completed_at (and only ran
on submit), so every read of a distinct goal added a permanent entry.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.services import goal_service as gs_mod
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-cache", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_OLD = datetime.now(UTC) - timedelta(days=2)


def _row(goal_id: str, status: str = "complete") -> Any:
    return SimpleNamespace(
        id=goal_id,
        goal_text="g",
        status=status,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=_OLD,
        updated_at=_OLD,
        completed_at=_OLD if status == "complete" else None,
        agent_id=None,
        workflow_mode="single_agent",
        execution_context={},
    )


def _db() -> Any:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        session = MagicMock()

        async def _execute(stmt: Any, *_a: Any, **_k: Any) -> Any:
            gid = stmt.compile().params.get("id_1")
            res = MagicMock()
            res.scalar_one_or_none = MagicMock(return_value=_row(str(gid)))
            return res

        session.execute = _execute

        @asynccontextmanager
        async def _begin() -> AsyncIterator[None]:
            yield None

        session.begin = _begin
        yield session

    return _factory


async def test_db_loaded_terminal_record_carries_completed_at() -> None:
    svc = GoalService(db_session_factory=_db())
    rec = await svc._db_get_goal_record("g-one", T)
    assert rec is not None and rec.completed_at


async def test_reading_many_distinct_goals_keeps_the_cache_bounded(monkeypatch: Any) -> None:
    monkeypatch.setattr(gs_mod, "_MAX_CACHED_GOALS", 500)
    svc = GoalService(db_session_factory=_db())
    for i in range(2_000):
        await svc._db_get_goal_record(f"g{i}", T)
    assert len(svc._goals) <= 500


async def test_cap_never_evicts_live_records(monkeypatch: Any) -> None:
    monkeypatch.setattr(gs_mod, "_MAX_CACHED_GOALS", 10)
    svc = GoalService(db_session_factory=_db())
    live = GoalRecord(
        goal_id="live",
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    live.task = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
    watched = GoalRecord(
        goal_id="watched",
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    watched.subscribers.append(asyncio.Queue())
    svc._goals["live"] = live
    svc._goals["watched"] = watched
    for i in range(50):
        await svc._db_get_goal_record(f"g{i}", T)
    assert "live" in svc._goals and "watched" in svc._goals
    live.task.cancel()  # type: ignore[union-attr]


async def test_terminal_record_without_timestamp_is_evicted_after_ttl(monkeypatch: Any) -> None:
    svc = GoalService(task_queue=MagicMock())
    svc._goals["stub"] = GoalRecord(
        goal_id="stub",
        goal_text="",
        status=GoalStatus.COMPLETE,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    svc._evict_stale_goals()  # first sight: stamped, kept
    assert "stub" in svc._goals
    monkeypatch.setattr(gs_mod, "_COMPLETED_GOAL_TTL_SECONDS", -1)
    svc._evict_stale_goals()
    assert "stub" not in svc._goals
