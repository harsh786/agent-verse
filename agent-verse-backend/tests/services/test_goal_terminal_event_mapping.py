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

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

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


def test_worker_envelope_on_the_goal_channel_maps_to_its_real_status() -> None:
    """The per-goal channel path (a08-F190-07/08) normalises the worker envelope
    and ends the stream only for a real terminal status."""
    norm = GoalService._normalize_bus_event
    status = GoalService._status_from_events

    def envelope(event: dict[str, Any]) -> dict[str, Any]:
        return {"goal_id": "g1", "tenant_id": "t1", "type": event["type"], "payload": event}

    waiting = norm(envelope({"type": "worker_complete", "status": "waiting_human"}))
    failed = norm(envelope({"type": "worker_complete", "status": "failed"}))
    assert waiting == {"type": "worker_complete", "status": "waiting_human"}
    assert status([waiting]) is None
    assert status([failed]) == GoalStatus.FAILED
