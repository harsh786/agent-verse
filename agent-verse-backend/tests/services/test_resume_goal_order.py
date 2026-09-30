"""WF-18: resume_goal moves a suspended goal out of waiting_human BEFORE it
re-enqueues run_goal.

``_relaunch_suspended_goal`` used to enqueue run_goal while the row was still
``waiting_human`` and flip it to ``executing`` only afterwards. That ordering
forced the worker's claim to accept waiting_human rows — which let any
redelivered message un-pause a goal no human had approved. Now the durable
status is ``executing`` before the message exists (so the tightened claim can
exclude waiting_human), and a failed enqueue puts the goal back to waiting.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import _SUSPENDED_KEY, GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="tid-wf18", plan=PlanTier.PROFESSIONAL, api_key_id="kid")


class _OrderedService(GoalService):
    def __init__(self, order: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._order = order

    async def _db_update_goal_status(self, goal_id: str, tenant_id: str, status: str,
                                     *args: Any, **kwargs: Any) -> Any:
        self._order.append(f"status:{status}")
        return True

    async def _db_set_suspended(self, goal_id: str, tenant_id: str, suspended: bool) -> None:
        self._order.append(f"suspended:{suspended}")

    async def _db_set_runner(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Queue:
    def __init__(self, order: list[str], fail: bool = False) -> None:
        self._order, self._fail = order, fail

    def enqueue_goal(self, **kwargs: Any) -> str:
        self._order.append("enqueue")
        if self._fail:
            raise ConnectionError("broker down")
        return "task"


def _suspended(svc: GoalService) -> GoalRecord:
    record = GoalRecord(
        goal_id="g-wf18", goal_text="deploy", status=GoalStatus.WAITING_HUMAN,
        tenant_id=CTX.tenant_id, priority="normal", dry_run=False,
        created_at="2026-01-01T00:00:00",
    )
    record.execution_context[_SUSPENDED_KEY] = True
    svc._goals[record.goal_id] = record
    return record


async def test_status_leaves_waiting_human_before_run_goal_is_enqueued() -> None:
    order: list[str] = []
    svc = _OrderedService(order, task_queue=_Queue(order))
    record = _suspended(svc)

    with patch("app.tenancy.limits.check_and_increment_concurrent_goals", new=AsyncMock()):
        result = await svc.resume_goal("g-wf18", CTX, approved=True)

    assert result["status"] == "resumed"
    assert "status:executing" in order and "enqueue" in order
    assert order.index("status:executing") < order.index("enqueue"), order
    assert record.status == GoalStatus.EXECUTING


async def test_failed_enqueue_puts_the_goal_back_to_waiting_human() -> None:
    order: list[str] = []
    svc = _OrderedService(order, task_queue=_Queue(order, fail=True))
    record = _suspended(svc)
    dec = AsyncMock()

    with (
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", new=AsyncMock()),
        patch("app.tenancy.limits.decrement_concurrent_goals", new=dec),
        pytest.raises(ConnectionError),
    ):
        await svc.resume_goal("g-wf18", CTX, approved=True)

    # Still resumable: waiting, suspended, and the slot it took is handed back.
    assert record.status == GoalStatus.WAITING_HUMAN
    assert record.execution_context.get(_SUSPENDED_KEY) is True
    assert order[-2:] == ["status:waiting_human", "suspended:True"], order
    dec.assert_awaited_once()
