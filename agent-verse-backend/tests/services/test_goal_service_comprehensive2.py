"""Comprehensive tests for app/services/goal_service.py — targeting 80%+ coverage.

Focuses on uncovered methods: pause_goal, resume_goal, cancel_goal, get_goal,
get_eval, list_goals, get_metrics, handle_approval, subscribe_events, get_events.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import NotFoundError
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalService
from app.tenancy.context import TenantContext, PlanTier


def _ctx(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


def _make_service() -> GoalService:
    return GoalService(
        audit_log=AuditLog(),
        hitl=HITLGateway(),
    )


def _inject_goal(
    svc: GoalService,
    goal_id: str,
    tenant_id: str = "t1",
    status: str = "executing",
    goal_text: str = "Do something",
) -> Any:
    """Inject a GoalRecord directly into service._goals for testing."""
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord

    record = GoalRecord(
        goal_id=goal_id,
        goal_text=goal_text,
        status=GoalStatus(status),
        tenant_id=tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals[goal_id] = record
    return record


# ── get_goal ──────────────────────────────────────────────────────────────────

class TestGetGoal:
    async def test_get_goal_returns_dict(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1")
        result = await svc.get_goal("g1", _ctx())
        assert result["goal_id"] == "g1"
        assert "status" in result
        assert "goal" in result

    async def test_get_goal_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.get_goal("nonexistent", _ctx())

    async def test_get_goal_wrong_tenant_raises(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", tenant_id="t1")
        with pytest.raises(NotFoundError):
            await svc.get_goal("g1", _ctx("t2"))

    async def test_get_goal_includes_expected_fields(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g2", goal_text="Analyze data")
        result = await svc.get_goal("g2", _ctx())
        assert result["goal"] == "Analyze data"
        assert "priority" in result
        assert "dry_run" in result
        assert "created_at" in result


# ── cancel_goal ───────────────────────────────────────────────────────────────

class TestCancelGoal:
    async def test_cancel_executing_goal(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="executing")
        result = await svc.cancel_goal("g1", _ctx())
        assert result["status"] == "cancelled"

    async def test_cancel_goal_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.cancel_goal("nonexistent", _ctx())

    async def test_cancel_cancels_asyncio_task(self) -> None:
        svc = _make_service()
        record = _inject_goal(svc, "g1", status="executing")
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        mock_task.cancel = MagicMock()
        record.task = mock_task

        await svc.cancel_goal("g1", _ctx())
        mock_task.cancel.assert_called_once()

    async def test_cancel_already_done_task_not_cancelled(self) -> None:
        svc = _make_service()
        record = _inject_goal(svc, "g1", status="executing")
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=True)
        mock_task.cancel = MagicMock()
        record.task = mock_task

        await svc.cancel_goal("g1", _ctx())
        mock_task.cancel.assert_not_called()

    async def test_cancel_signals_redis_when_available(self) -> None:
        svc = _make_service()
        mock_redis = AsyncMock()
        svc._redis = mock_redis
        _inject_goal(svc, "g1", status="executing")

        await svc.cancel_goal("g1", _ctx())
        mock_redis.set.assert_called()

    async def test_cancel_planning_goal(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="planning")
        result = await svc.cancel_goal("g1", _ctx())
        assert result["status"] == "cancelled"


# ── pause_goal ────────────────────────────────────────────────────────────────

class TestPauseGoal:
    async def test_pause_executing_goal(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="executing")
        result = await svc.pause_goal("g1", _ctx())
        assert result["status"] == "paused"

    async def test_pause_planning_goal(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="planning")
        result = await svc.pause_goal("g1", _ctx())
        assert result["status"] == "paused"

    async def test_pause_non_running_goal_raises(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="complete")
        with pytest.raises(ValueError, match="not running"):
            await svc.pause_goal("g1", _ctx())

    async def test_pause_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.pause_goal("nonexistent", _ctx())

    async def test_pause_signals_redis_when_available(self) -> None:
        svc = _make_service()
        mock_redis = AsyncMock()
        svc._redis = mock_redis
        _inject_goal(svc, "g1", status="executing")

        await svc.pause_goal("g1", _ctx())
        mock_redis.set.assert_called()


# ── resume_goal ───────────────────────────────────────────────────────────────

class TestResumeGoal:
    async def test_resume_approved(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="waiting_human")
        result = await svc.resume_goal("g1", _ctx(), approved=True)
        assert "status" in result

    async def test_resume_rejected_fails_goal(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="waiting_human")
        result = await svc.resume_goal("g1", _ctx(), approved=False, feedback="Not safe")
        assert result["status"] == "rejected"

    async def test_resume_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.resume_goal("nonexistent", _ctx())

    async def test_resume_terminal_goal_raises(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="complete")
        with pytest.raises(ValueError, match="terminal"):
            await svc.resume_goal("g1", _ctx())

    async def test_resume_rejected_stores_feedback(self) -> None:
        svc = _make_service()
        record = _inject_goal(svc, "g1", status="waiting_human")
        await svc.resume_goal("g1", _ctx(), approved=False, feedback="Denied by operator")
        assert record.execution_context.get("hitl_rejected") is True
        assert "Denied by operator" in record.execution_context.get("hitl_feedback", "")

    async def test_resume_approved_stores_feedback(self) -> None:
        svc = _make_service()
        record = _inject_goal(svc, "g1", status="waiting_human")
        await svc.resume_goal("g1", _ctx(), approved=True, feedback="LGTM")
        assert record.execution_context.get("hitl_approved") is True


# ── list_goals ────────────────────────────────────────────────────────────────

class TestListGoals:
    async def test_list_goals_empty(self) -> None:
        svc = _make_service()
        result = await svc.list_goals(_ctx())
        assert result["goals"] == []

    async def test_list_goals_returns_tenant_goals(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", tenant_id="t1")
        _inject_goal(svc, "g2", tenant_id="t2")
        result = await svc.list_goals(_ctx("t1"))
        goal_ids = [g["goal_id"] for g in result["goals"]]
        assert "g1" in goal_ids
        assert "g2" not in goal_ids

    async def test_list_goals_multiple_goals(self) -> None:
        svc = _make_service()
        for i in range(3):
            _inject_goal(svc, f"g{i}", tenant_id="t1")
        result = await svc.list_goals(_ctx("t1"))
        assert len(result["goals"]) == 3

    async def test_list_goals_response_fields(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1")
        result = await svc.list_goals(_ctx())
        goal = result["goals"][0]
        assert "goal_id" in goal
        assert "status" in goal
        assert "goal" in goal


# ── get_eval ──────────────────────────────────────────────────────────────────

class TestGetEval:
    async def test_get_eval_not_evaluated(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1")
        result = await svc.get_eval("g1", _ctx())
        assert result["status"] == "not_evaluated"
        assert result["goal_id"] == "g1"

    async def test_get_eval_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.get_eval("nonexistent", _ctx())


# ── get_metrics ───────────────────────────────────────────────────────────────

class TestGetMetrics:
    async def test_get_metrics_returns_dict(self) -> None:
        svc = _make_service()
        result = await svc.get_metrics(_ctx())
        assert "active_goals" in result
        assert "success_rate" in result

    async def test_get_metrics_with_goals(self) -> None:
        svc = _make_service()
        _inject_goal(svc, "g1", status="complete")
        _inject_goal(svc, "g2", status="failed")
        _inject_goal(svc, "g3", status="executing")
        result = await svc.get_metrics(_ctx())
        assert result["active_goals"] >= 1
        assert result["total_goals"] >= 3


# ── get_events ────────────────────────────────────────────────────────────────

class TestGetEvents:
    async def test_get_events_for_goal(self) -> None:
        svc = _make_service()
        record = _inject_goal(svc, "g1")
        record.events.append({"type": "step_started", "ts": datetime.now(UTC).isoformat()})
        result = await svc.get_events("g1", _ctx())
        assert isinstance(result, list)

    async def test_get_events_not_found_raises(self) -> None:
        svc = _make_service()
        with pytest.raises(NotFoundError):
            await svc.get_events("nonexistent", _ctx())


# ── handle_approval ───────────────────────────────────────────────────────────

class TestHandleApproval:
    async def test_approve_hitl_request(self) -> None:
        svc = _make_service()
        ctx = _ctx()
        record = _inject_goal(svc, "g1", status="waiting_human")

        # Register request in HITL gateway
        req = svc._hitl.request_approval(
            goal_id="g1", action="deploy", tenant_ctx=ctx
        )

        result = await svc.handle_approval(
            goal_id="g1",
            request_id=req.request_id,
            action="approve",
            approver="alice",
            note="LGTM",
            tenant_ctx=ctx,
        )
        assert result["action"] == "approve"
        assert bool(result["accepted"]) is True

    async def test_reject_hitl_request(self) -> None:
        svc = _make_service()
        ctx = _ctx()
        _inject_goal(svc, "g1", status="waiting_human")

        req = svc._hitl.request_approval(
            goal_id="g1", action="deploy", tenant_ctx=ctx
        )

        result = await svc.handle_approval(
            goal_id="g1",
            request_id=req.request_id,
            action="reject",
            approver="bob",
            note="Not safe",
            tenant_ctx=ctx,
        )
        assert result["action"] == "reject"
        assert result["accepted"] is True
