"""SVC-03: HITL resume/reject and cancel status writes are conditional and honest.

* resume_goal's reject (FAILED) and non-suspended approve (EXECUTING) writes
  used neither only_if_active nor raise_on_error: a DB error was swallowed while
  the API reported success, and a goal that finished meanwhile was overwritten.
* cancel/reject signalled the runner and then failed to persist, leaving the
  runner cancelled while the API answered 503.
* every status write zeroed ``iterations``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import ServiceUnavailableError
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-resume", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.data.pop(k, None) is not None)

    async def publish(self, *_a: Any) -> int:
        return 0


def _failing_db() -> Any:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        raise ConnectionError("db down")
        yield  # pragma: no cover

    return _factory


class _Recording:
    """Session factory capturing UPDATE parameters; rowcount configurable."""

    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.updates: list[dict[str, Any]] = []

    def __call__(self) -> Any:
        outer = self

        @asynccontextmanager
        async def _cm() -> AsyncIterator[Any]:
            session = MagicMock()

            async def _execute(stmt: Any, *_a: Any, **_k: Any) -> Any:
                if getattr(stmt, "is_update", False):
                    compiled = stmt.compile()
                    outer.updates.append(dict(compiled.params))
                return MagicMock(rowcount=outer.rowcount)

            session.execute = _execute

            @asynccontextmanager
            async def _begin() -> AsyncIterator[None]:
                yield None

            session.begin = _begin
            yield session

        return _cm()


def _waiting(goal_id: str = "g-wait") -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="deploy",
        status=GoalStatus.WAITING_HUMAN,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _svc(db: Any, record: GoalRecord, fresh: GoalRecord | None = None) -> GoalService:
    svc = GoalService(db_session_factory=db, task_queue=MagicMock())
    svc._redis = _Redis()
    svc._goals[record.goal_id] = record
    svc._db_get_goal_record = AsyncMock(return_value=fresh or record)  # type: ignore[method-assign]
    svc._dispatch_event = AsyncMock()  # type: ignore[method-assign]
    return svc


async def test_reject_with_db_down_is_503_and_rolls_back_the_cancel_signal() -> None:
    record = _waiting()
    svc = _svc(_failing_db(), record)
    with pytest.raises(ServiceUnavailableError):
        await svc.resume_goal(record.goal_id, T, approved=False, feedback="no")
    svc._dispatch_event.assert_not_awaited()
    assert record.status == GoalStatus.WAITING_HUMAN
    assert not any(k.startswith("goal_cancelled:") for k in svc._redis.data)


async def test_approve_with_db_down_is_503() -> None:
    record = _waiting()
    svc = _svc(_failing_db(), record)
    with pytest.raises(ServiceUnavailableError):
        await svc.resume_goal(record.goal_id, T, approved=True)
    svc._dispatch_event.assert_not_awaited()


async def test_approve_on_goal_that_finished_meanwhile_reports_real_status() -> None:
    record = _waiting()
    finished = replace(record, status=GoalStatus.COMPLETE)
    svc = _svc(_Recording(rowcount=0), record)
    svc._db_get_goal_record = AsyncMock(side_effect=[record, finished])  # type: ignore[method-assign]
    result = await svc.resume_goal(record.goal_id, T, approved=True)
    assert result["status"] == "complete"
    svc._dispatch_event.assert_not_awaited()


async def test_reject_on_goal_that_finished_meanwhile_reports_real_status() -> None:
    record = _waiting()
    finished = replace(record, status=GoalStatus.COMPLETE)
    svc = _svc(_Recording(rowcount=0), record)
    svc._db_get_goal_record = AsyncMock(side_effect=[record, finished])  # type: ignore[method-assign]
    result = await svc.resume_goal(record.goal_id, T, approved=False)
    assert result["status"] == "complete"
    svc._dispatch_event.assert_not_awaited()


async def test_cancel_with_db_down_rolls_back_the_cancel_signal() -> None:
    record = replace(_waiting(), status=GoalStatus.EXECUTING)
    svc = _svc(_failing_db(), record)
    with pytest.raises(ServiceUnavailableError):
        await svc.cancel_goal(record.goal_id, T)
    assert not any(k.startswith("goal_cancelled:") for k in svc._redis.data)


async def test_status_writes_leave_iterations_untouched() -> None:
    db = _Recording()
    record = replace(_waiting(), status=GoalStatus.EXECUTING)
    svc = _svc(db, record)
    await svc.cancel_goal(record.goal_id, T)
    assert db.updates and all("iterations" not in u for u in db.updates)
