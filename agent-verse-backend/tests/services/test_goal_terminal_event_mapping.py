"""Terminal SSE/status events must reflect the real outcome of the run.

Regressions:
- ``_run_workflow`` emitted ``goal_complete`` after ``WorkflowExecutor.execute``
  even when the executor returned ``{"status": "failed"}``.
- The Celery worker's ``worker_complete`` event carries the final ``status``
  (complete / failed / waiting_human), but the SSE bridge and the replay
  status helper mapped every ``worker_complete`` to COMPLETE and closed the
  stream — so a failed or approval-suspended goal looked completed.
- The isolated-execution path set FAILED on an unsuccessful result without
  emitting any terminal event, leaving SSE streams open forever.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _inject(svc: GoalService, status: GoalStatus = GoalStatus.EXECUTING) -> GoalRecord:
    rec = GoalRecord(
        goal_id="g1",
        goal_text="do it",
        status=status,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals["g1"] = rec
    return rec


# ── _run_workflow ──────────────────────────────────────────────────────────


class _FailingExecutor:
    def __init__(self, **_: Any) -> None:
        pass

    async def execute(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"status": "failed", "reason": "step s2 failed: boom", "results": {}}


class _OkExecutor(_FailingExecutor):
    async def execute(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"status": "complete", "summary": "done", "results": {}}


@pytest.mark.asyncio
async def test_workflow_failure_emits_goal_failed_not_goal_complete() -> None:
    svc = GoalService()
    rec = _inject(svc)
    with patch("app.services.goal_service.WorkflowExecutor", _FailingExecutor):
        await svc._run_workflow("g1", "do it", _ctx())
    types = [e["type"] for e in rec.events]
    assert "goal_complete" not in types
    assert types[-1] == "goal_failed"
    assert "boom" in rec.events[-1]["reason"]
    assert rec.status == GoalStatus.FAILED


@pytest.mark.asyncio
async def test_workflow_success_still_emits_goal_complete() -> None:
    svc = GoalService()
    rec = _inject(svc)
    with patch("app.services.goal_service.WorkflowExecutor", _OkExecutor):
        await svc._run_workflow("g1", "do it", _ctx())
    assert rec.events[-1]["type"] == "goal_complete"
    assert rec.status == GoalStatus.COMPLETE


# ── worker_complete status mapping ─────────────────────────────────────────


def test_status_from_events_honours_worker_complete_status() -> None:
    f = GoalService._status_from_events
    assert f([{"type": "worker_complete", "status": "complete"}]) == GoalStatus.COMPLETE
    assert f([{"type": "worker_complete", "status": "failed"}]) == GoalStatus.FAILED
    assert f([{"type": "worker_complete", "status": "waiting_human"}]) is None
    # legacy event without status keeps the historic meaning
    assert f([{"type": "worker_complete"}]) == GoalStatus.COMPLETE


class _FakePubSub:
    def __init__(self, msgs: list[dict[str, Any]]) -> None:
        self._msgs = msgs

    async def psubscribe(self, *_: Any) -> None:
        return None

    async def listen(self) -> Any:
        for m in self._msgs:
            yield m
        await asyncio.Event().wait()


class _FakeRedis:
    def __init__(self, msgs: list[dict[str, Any]]) -> None:
        self._msgs = msgs

    async def __aenter__(self) -> _FakeRedis:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._msgs)


def _bridge_msg(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "pmessage",
        "data": json.dumps(
            {"goal_id": "g1", "tenant_id": "t1", "type": event["type"], "payload": event}
        ),
    }


async def _run_bridge(svc: GoalService, events: list[dict[str, Any]]) -> None:
    with patch("redis.asyncio.from_url", return_value=_FakeRedis([_bridge_msg(e) for e in events])):
        task = asyncio.create_task(svc._subscribe_celery_goal_events("redis://fake"))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_bridge_worker_complete_waiting_human_is_not_terminal() -> None:
    svc = GoalService()
    rec = _inject(svc)
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    rec.subscribers.append(q)
    await _run_bridge(svc, [{"type": "worker_complete", "status": "waiting_human"}])
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert None not in items, "stream closed on an approval-suspended goal"
    assert rec.status != GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_bridge_worker_complete_failed_marks_failed_and_closes() -> None:
    svc = GoalService()
    rec = _inject(svc)
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    rec.subscribers.append(q)
    await _run_bridge(svc, [{"type": "worker_complete", "status": "failed"}])
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert items[-1] is None
    assert rec.status == GoalStatus.FAILED


# ── isolated execution ─────────────────────────────────────────────────────


class _Scheduler:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def schedule(self, *_: Any, **__: Any) -> Any:
        return self._result


def _iso_result(success: bool, status: str, error: str = "") -> Any:
    from app.execution_environment.models import ExecutionResult

    return ExecutionResult(
        goal_id="g1",
        tenant_id="t1",
        attempt_id="a1",
        success=success,
        status=status,
        error_message=error,
    )


@pytest.mark.asyncio
async def test_isolated_failure_without_runner_event_emits_goal_failed() -> None:
    svc = GoalService(
        app_state=SimpleNamespace(
            execution_scheduler=_Scheduler(_iso_result(False, "failed", "policy denied"))
        )
    )
    rec = _inject(svc)
    await svc._run_agent_loop_isolated("g1", "do it", _ctx())
    assert rec.events[-1]["type"] == "goal_failed"
    assert rec.events[-1]["reason"] == "policy denied"
    assert rec.status == GoalStatus.FAILED


@pytest.mark.asyncio
async def test_isolated_waiting_human_suspends_without_terminal_event() -> None:
    svc = GoalService(
        app_state=SimpleNamespace(
            execution_scheduler=_Scheduler(_iso_result(False, "waiting_human"))
        )
    )
    rec = _inject(svc)
    svc._suspend_for_approval = AsyncMock()  # type: ignore[method-assign]
    await svc._run_agent_loop_isolated("g1", "do it", _ctx())
    assert not any(
        e["type"] in {"goal_complete", "goal_failed", "goal_cancelled"} for e in rec.events
    )
    svc._suspend_for_approval.assert_awaited_once()
    assert rec.status != GoalStatus.FAILED
